# Sharing ContractLens with other people

ContractLens is a Windows desktop program. You share it as an installer; each person installs it and signs in.

## 1. Build the installer (you, once per release)

Use a clean Python environment so the installer stays small (a busy Python install makes PyInstaller pull in unrelated libraries):

```
python -m venv build_env
build_env\Scripts\activate
set HNSWLIB_NO_NATIVE=1
pip install -r requirements.txt pyinstaller
python -m PyInstaller ContractLens.spec --noconfirm      # about 15 minutes; makes dist\ContractLens\
```

Then wrap it into one setup file with [Inno Setup](https://jrsoftware.org/isinfo.php) (free, `winget install JRSoftware.InnoSetup`):

```
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\ContractLens.iss
```

Result: `dist\ContractLens-Setup.exe`. That single file is what you send to people.

## 2. What gets installed, and where user data lives

| Item | Location |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\ContractLens` (or Program Files if the user chooses "all users") |
| Settings file (`.env`) | `%APPDATA%\ContractLens\.env`, created from the bundled example on first start |
| Local data, logs, index | `%APPDATA%\ContractLens\data\` |

Uninstalling does not delete `%APPDATA%\ContractLens`, so people keep their data.

## 3. Before you share (checklist)

1. **No secrets in the installer.** The build bundles only `.env.example`, which has empty key fields. Check that it still does. Never copy your own `.env` into the build.
2. **Rotate any key that was ever pasted into chat or committed** (OpenAI, Anthropic, Gemini, Supabase secret and service-role keys).
3. **Give users a shared backend, not your keys.**
   - Supabase project with the migrations applied (`migrations/ALL_IN_ONE.sql`) and RLS on.
   - Deploy the `openai-proxy` Edge Function so the AI key stays on the server (`docs/DEPLOYMENT.md`).
   - Ship a pre-filled settings file that contains only `SUPABASE_URL`, the **anon** key and `OPENAI_BASE_URL`. The anon key is designed to be public; the service-role key must never be included.
4. **Shared search index for teams:** run a Chroma server and set `CHROMA_MODE=http`.
5. **Test the live Supabase path with two real accounts** in two organisations, and confirm neither can see the other's contracts.
6. **Licence:** PyQt6 is GPL or commercial. A closed-source or commercial release needs a Riverbank commercial licence, or a move to PySide6 (LGPL).
7. **Windows warning.** An unsigned installer triggers "Windows protected your PC" (SmartScreen). Sign it with a code-signing certificate to remove the warning. Without one, users click "More info", then "Run anyway".

## 4. Ways to hand it over

- Send `ContractLens-Setup.exe` by a file-sharing link (OneDrive, Google Drive, a company share) or attach it to a GitHub Release.
- Do **not** commit the installer to the Git repository (it is hundreds of MB). `dist/` and `build/` are git-ignored.
- For a company: your IT team can deploy it silently with `ContractLens-Setup.exe /VERYSILENT`.

## 5. First run for a new user

1. Install and start ContractLens.
2. If you supplied a settings file, put it at `%APPDATA%\ContractLens\.env` (or use the one created on first start and edit it).
3. Sign in with the Supabase account you created for them. With no Supabase settings, the app runs in local demo mode.
