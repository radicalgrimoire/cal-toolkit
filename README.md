# Calendar Toolkit

Calendar Toolkit turns an event overview or schedule page into an iCalendar (`.ics`) file. It fetches the page, asks Gemini to extract dated events, validates the returned JSON, and writes calendar entries in Japan Standard Time.

## What It Creates

The generated calendar is [`calendar/calender.ics`](calendar/calender.ics). Events include a title, date, opening time, start time, location, and an end time. When the source does not specify an end time, the default duration is three hours.

Each run also stores the input and intermediate output:

- `calendar/event-source.html`: the fetched event page
- `calendar/event-sources/latest.json`: the validated event data returned by Gemini

On a subsequent run, events generated from the previous source are replaced. Events in the ICS file that were not created by this toolkit are preserved.

## Use with GitHub Actions

1. Create a Gemini API key in Google AI Studio.
2. In the repository settings, add it as the `GEMINI_APIKEY` Actions secret.
3. Ensure GitHub Actions has permission to write repository contents.
4. Open the **Actions** tab and run **Add Calendar Events from a Web Page**.
5. Enter an HTTPS URL for the page that contains the event overview or schedule.

The workflow commits the refreshed ICS file, source HTML, and JSON to the repository.

Set the `CALENDAR_NAME` value in [`.github/workflows/add-calendar-event.yml`](.github/workflows/add-calendar-event.yml) to choose the display name shown by calendar clients.

## Run Locally

Python 3.11 or later is recommended. The toolkit uses only the Python standard library.

Set `GEMINI_APIKEY`, then run:

```powershell
$env:GEMINI_APIKEY = "your-gemini-api-key"
python scripts/add_ics_event.py `
  --source calendar/event-source.html `
  --json-output calendar/event-sources/latest.json `
  --calendar-name "LifeLogs" `
  --output calendar/calender.ics
```

To bypass Gemini and generate an ICS file from an existing JSON file:

```powershell
python scripts/add_ics_event.py `
  --events-json calendar/event-sources/latest.json `
  --calendar-name "LifeLogs" `
  --output calendar/calender.ics
```

The input JSON must use schema version 1 and provide `title`, `date`, `open_at`, `start_at`, `end_at`, and `location` for every event. Dates use `YYYY-MM-DD`; times use `HH:MM`.

## Notes

- The source URL must be HTTPS when using the GitHub Actions workflow.
- The event parser does not invent dates, times, or venues that are absent from the source page.
- The calendar uses the `Asia/Tokyo` timezone and writes UTC timestamps to the ICS file.