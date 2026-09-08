# Local speech interpretation in Command

Command automatically processes incoming PTT broadcasts and chat messages in the background, including teams hosted on another device. Local Whisper transcribes the recording; a local Ollama language model interprets the transcript and selects marker types and location references. The browser no longer uses its deterministic speech parser to create markers.

Run `./start-local-ai.ps1` once from Windows PowerShell with Docker Desktop running. It starts the local model service and downloads the default model. Docker retains the model in the `language-models` volume and restarts the service automatically. Both the native Windows application and Docker Command use this service. No API key is required. Windows builds include faster-whisper.

The default model is `qwen3:4b-instruct`. Override `RETICOM_AI_MODEL` and install that same model in Ollama to use another compatible model. `RETICOM_AI_URL` defaults to `http://127.0.0.1:11434`; Docker Command uses the internal service address. Set `RETICOM_AI_ENABLED=0` to disable automatic speech interpretation. CPU inference is supported, but queued reports can take tens of seconds each on a modest PC.

The model interprets paraphrases, spoken numbers, metre and klick units, named operators, meeting places, multiple observations and cancellation of the speaker's previous AI marker. Examples include “let's meet by the nearest church”, “heat signature two hundred metres north of my position”, and “casualty fifty metres east of Bravo”. It receives recent reports from the same speaker for context. This is language interpretation, so transcription or interpretation mistakes remain possible.

The model returns structured location references, never map coordinates. Application code validates these references, uses a position fix from within ten minutes of the broadcast, converts compass directions and distances, and resolves landmarks. Existing team marker names work locally; unknown places use OpenStreetMap within five kilometres and require connectivity. Only the landmark lookup goes to that map service; speech and model inference stay local.

Public-report markers are signed and shared through the existing team transport, with the source broadcast attached and stable IDs to prevent duplicates on retries. Temporary observations expire after thirty minutes; other automatic markers after two hours. Only broadcasts from the last thirty minutes are considered. Radio checks, negative reports and ambiguous locations do not produce guessed markers. Command displays transcription, interpretation, completion and error status above its communications panel; individual transcripts show their interpretation result.

Run the focused regression checks with `python -m pytest tests/test_speech_markers.py tests/test_speech_worker.py tests/test_protocol.py tests/test_store.py tests/test_transcription.py tests/test_command_teams.py -q` and `node --test tests/automatic-reports.test.mjs`. Real model output must also be checked when changing the model or prompt; mocked tests only verify grounding and publication behavior.

## Command and recipient origins

Right-click Command's map and choose **Set Command position here**. This saves a fixed origin per team and displays a Command position marker; GPS expiry does not apply to this explicitly fixed point. Moving it replaces the previous marker.

- Command: "Helo 1 click north" uses the fixed Command point.
- Command in team chat: "Helo 1 click north of your position" uses the mean of each field operator's latest fresh fix, excluding Command. Repeated fixes do not add weight; fixes older than ten minutes at report time are excluded.
- In a DM: "your position" uses the recipient alone. A missing recipient fix never falls back to the group.

DM reports are interpreted separately from public chat. Their derived markers stay in Command's local storage and appear when the corresponding DM is open; they are never published into the shared team feed. They are not transmitted as markers to the recipient's Field app. Typed reports show their AI result directly below the message. Missing fixed positions are explained there; setting the origin retries recent reports waiting for that point.
