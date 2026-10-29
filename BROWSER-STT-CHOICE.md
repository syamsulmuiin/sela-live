# Optional browser STT beside local speech

## Applied behavior

`VOICE_ALLOW_BROWSER_STT=1` permits the existing browser-recognition choice even while `VOICE_STT_BACKEND=local` remains active. The flag defaults to `0`; only `0` and `1` are accepted. Browser-only mode remains an explicit separate configuration.

The deployed systemd drop-in enables the optional choice. It does not select it for the user, change the Codex provider, change the Piper voice, replace local models, or bypass login. There is no automatic fallback to a vendor when local STT fails.

## Use

1. End any existing microphone session and reload the page.
2. Check **Pengenalan suara daring browser (eksperimental)**, then press **Mulai percakapan**.
3. To return to local STT, end the session, uncheck the option, then start again.

Before consent, no vendor recognizer starts. Help/privacy text states that browser audio may go to the browser vendor. The checkbox is locked while starting/running. Unsupported browsers retain local STT/text access. Browser startup/service failure gives an explicit route back to local STT and does not open its microphone automatically. Browser recognition uses Indonesian and half-duplex hands-free mode; PTT is unavailable in that mode.

## Verification

- New behavior was tested RED -> GREEN at both Speech capability and shipping-client boundaries.
- Python suite: `Ran 77 tests`, `OK (skipped=3)`.
- Node suite: `91 passed`, `0 failed`.
- Real authenticated HTTP/WebSocket integration: actual local ASR turn, actual Codex/Piper, then a browser-transcript fixture turn, retained history and playback ACK.
- Real isolated browser: initially unchecked/no recognizer; explicit choice; two fixture recognition turns; actual Codex/Piper rendering and playback ACKs; zero local PCM commands during browser mode. Mobile viewport: width=390, scrollWidth=390.
- The browser callbacks were fixtures, NOT actual vendor STT. This does not establish recognition accuracy on the user's microphone or successful external vendor service access. Physical speaker quality remains unverified.
- Production restart/readback: service active, process environment has browser flag 1 while STT remains local/Piper, public health 200, unauthenticated config 401, protected JS 303 to login.
- See `verification/browser-choice-browser.json`, `verification/browser-choice-deployment.json` and `verification/web-api-smoke.json`.
- Isolated fixture process and its listener were stopped; production remains on loopback port 8091.

## Remaining scope

This restores a supported-browser route for practical use and comparison. It does not fix the known local-model word substitution or demonstrate a recovered browser/server speech model. Public-source investigation of the browser engine is a separate evidence stream; no proprietary model or service credentials were extracted.
