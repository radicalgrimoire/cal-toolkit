#!/usr/bin/env python3
"""自然言語の予定を JSON 経由でコピー用 ICS カレンダーへ追加する。"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
SOURCE_ID = "calendar/event-source.md"


def escape_ics(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def request_events(source: str, year: int, api_key: str) -> dict:
    prompt = "\n".join(
        (
            "あなたは日本の公演・予定表を構造化するアシスタントです。",
            "入力文から予定を漏れなく抽出し、指定した JSON オブジェクトだけを返してください。",
            "推測で会場、日付、時刻を補わないでください。曜日は検証せず、年月日だけを使ってください。",
            "開場時刻があれば open_at に入れ、なければ開演時刻を入れてください。start_at は開演時刻です。",
            "同一地区に複数日程がある場合も、日程ごとに1件ずつ events に入れてください。",
            "終了時刻が明記されていれば end_at に入れてください。記載がない場合だけ空文字列にしてください。",
            '{"schema_version": 1, "title": "ツアー名", "events": [{"title": "ツアー名 埼玉公演", "date": "2027-01-23", "open_at": "16:00", "start_at": "17:00", "end_at": "20:00", "location": "会場名"}]}',
            f"既定年: {year}",
            "入力文:",
            source,
        )
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            text = json.load(response)["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API 呼び出しに失敗しました: HTTP {error.code} {payload[:400]}") from error
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Gemini の予定 JSON 生成に失敗しました: {error}") from error


def validate_events(contents: object) -> list[dict[str, str]]:
    if not isinstance(contents, dict) or contents.get("schema_version") != 1:
        raise ValueError("予定 JSON の schema_version が不正です")
    records = contents.get("events")
    if not isinstance(records, list) or not records:
        raise ValueError("予定 JSON に events がありません")

    fields = ("title", "date", "open_at", "start_at", "end_at", "location")
    events: list[dict[str, str]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict) or any(not isinstance(record.get(field), str) for field in fields):
            raise ValueError(f"予定 JSON の events[{index}] が不正です")
        event = {field: record[field].strip() for field in fields}
        if not event["title"] or not event["location"]:
            raise ValueError(f"予定 JSON の events[{index}] にタイトルまたは場所がありません")
        try:
            datetime.strptime(event["date"], "%Y-%m-%d")
            datetime.strptime(event["open_at"] or event["start_at"], "%H:%M")
            if event["start_at"]:
                datetime.strptime(event["start_at"], "%H:%M")
            if event["end_at"]:
                datetime.strptime(event["end_at"], "%H:%M")
        except ValueError as error:
            raise ValueError(f"予定 JSON の events[{index}] の日時形式が不正です") from error
        events.append(event)
    return events


def build_event(event: dict[str, str], duration: int) -> str:
    start_time = event["open_at"] or event["start_at"]
    start = datetime.strptime(f'{event["date"]} {start_time}', "%Y-%m-%d %H:%M")
    start_utc = start.replace(tzinfo=JST).astimezone(timezone.utc)
    if event["end_at"]:
        end = datetime.strptime(f'{event["date"]} {event["end_at"]}', "%Y-%m-%d %H:%M")
        end_utc = end.replace(tzinfo=JST).astimezone(timezone.utc)
    else:
        end_utc = start_utc + timedelta(minutes=duration)
    if end_utc <= start_utc:
        raise ValueError(f'終了時刻が開始時刻以前です: {event["title"]}')
    created_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    description = f'開演: {event["start_at"]}' if event["start_at"] else ""
    return "\r\n".join(
        (
            "BEGIN:VEVENT",
            f"UID:{uuid4()}@lifelogs.local",
            f"DTSTAMP:{created_at}",
            f"SUMMARY:{escape_ics(event['title'])}",
            f"DTSTART:{start_utc:%Y%m%dT%H%M%SZ}",
            f"DTEND:{end_utc:%Y%m%dT%H%M%SZ}",
            "SEQUENCE:0",
            "CLASS:PUBLIC",
            f"CREATED:{created_at}",
            f"LAST-MODIFIED:{created_at}",
            f"LOCATION:{escape_ics(event['location'])}",
            f"DESCRIPTION:{escape_ics(description)}",
            f"X-LIFELOGS-SOURCE:{SOURCE_ID}",
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        )
    )


def remove_source_events(calendar: str) -> str:
    pattern = re.compile(r"(?ms)^BEGIN:VEVENT\r?\n.*?^END:VEVENT\r?\n?")

    def keep_other_events(match: re.Match[str]) -> str:
        return "" if f"X-LIFELOGS-SOURCE:{SOURCE_ID}" in match.group() else match.group()

    return pattern.sub(keep_other_events, calendar)


def set_calendar_name(calendar: str, calendar_name: str) -> str:
    return re.sub(
        r"(?m)^X-WR-CALNAME:.*$",
        f"X-WR-CALNAME:{escape_ics(calendar_name)}",
        calendar,
        count=1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="自然言語の予定を JSON 経由で ICS カレンダーへ追加します。")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path, help="予定本文を保存した UTF-8 テキストファイル")
    source.add_argument("--events-json", type=Path, help="検証済み予定 JSON ファイル")
    parser.add_argument("--json-output", type=Path, help="Gemini が生成した予定 JSON の保存先")
    parser.add_argument("--year", type=int, default=datetime.now(JST).year, help="本文で年が省略された場合の年")
    parser.add_argument("--duration", type=int, default=180, help="終了時刻がない場合の所要時間（分）")
    parser.add_argument("--calendar-name", required=True, help="ICS カレンダーの表示名")
    parser.add_argument("--output", type=Path, required=True, help="出力先 ICS ファイル")
    args = parser.parse_args()

    if args.duration <= 0:
        parser.error("所要時間は1分以上にしてください。")
    if not args.calendar_name.strip():
        parser.error("カレンダー名を指定してください。")
    if args.events_json:
        contents = json.loads(args.events_json.read_text(encoding="utf-8"))
    else:
        api_key = os.environ.get("GEMINI_APIKEY", "").strip()
        if not api_key:
            parser.error("GEMINI_APIKEY が未設定です。")
        source_text = args.source.read_text(encoding="utf-8")
        if not source_text.strip():
            parser.error("予定本文が空です。calendar/event-source.md に内容を入れてください。")
        contents = request_events(source_text, args.year, api_key)
        if args.json_output is None:
            parser.error("--source の場合は --json-output が必要です。")
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(contents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    events = validate_events(contents)
    rendered_events = "\r\n".join(build_event(event, args.duration) for event in events)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        existing = args.output.read_text(encoding="utf-8").rstrip("\r\n")
        if not existing.endswith("END:VCALENDAR"):
            parser.error("出力先の ICS ファイルが不正です。")
        existing = set_calendar_name(remove_source_events(existing), args.calendar_name)
        calendar = f"{existing.removesuffix('END:VCALENDAR').rstrip()}\r\n{rendered_events}\r\nEND:VCALENDAR\r\n"
    else:
        calendar = "\r\n".join(
            (
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//LifeLogs//Copy Calendar//JA",
                "CALSCALE:GREGORIAN",
                f"X-WR-CALNAME:{escape_ics(args.calendar_name)}",
                "X-WR-TIMEZONE:Asia/Tokyo",
                rendered_events,
                "END:VCALENDAR",
                "",
            )
        )
    args.output.write_text(calendar, encoding="utf-8", newline="")


if __name__ == "__main__":
    main()