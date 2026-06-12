# Pan Majster

Testowa aplikacja webowa/PWA do dokumentowania prac terenowych: zdjęcia,
notatki głosowe, problemy, raporty i bezpieczne udostępnianie klientowi.

Projekt działa na osobnej gałęzi `pan-majster` tego samego repozytorium co
rejestrator czasu pracy. Nie zmienia usługi wdrażanej z gałęzi `main`.

## Zakres wersji testowej

- logowanie kodem e-mail bez hasła,
- konta samodzielne i organizacje,
- projekty, etapy, role, zaproszenia i odwoływalne linki gościnne,
- zdjęcia, nagrania, wpisy, komentarze i statusy problemów,
- lokalne szkice PWA i ponawianie wysyłki po odzyskaniu połączenia,
- transkrypcja oraz redakcja raportu przez OpenAI,
- zatwierdzanie raportów, link, PIN, QR i PDF,
- proste portfolio i panel dostępu testowego,
- trwała kolejka zadań w PostgreSQL,
- pliki testowe w PostgreSQL z rekordem `MediaAsset` i SHA-256.

## Uruchomienie lokalne

Backend wymaga Pythona 3.11 lub nowszego, frontend Node.js 22.

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt

cd frontend
npm ci
npm run build
cd ..

alembic upgrade head
uvicorn main:app --reload
```

Aplikacja: `http://localhost:8000`

API: `http://localhost:8000/docs`

Kontrola stanu: `http://localhost:8000/api/health`

W trybie `development`, jeśli SMTP nie jest skonfigurowane, endpoint logowania
zwraca `dev_code`. W produkcji kod jest wysyłany wyłącznie e-mailem.

Bezkosztowy pilotaż na Renderze działa tymczasowo z `APP_ENV=development`, więc
kod logowania jest pokazywany testerowi w aplikacji. Przed publicznym
uruchomieniem należy ustawić `APP_ENV=production` i skonfigurować SMTP.

## Testy

```powershell
pytest -q
cd frontend
npm run build
```

## Render

Plik `render.yaml` tworzy darmowy Web Service `pan-majster`, bez dodatkowej
bazy i bez Persistent Disk.

Web Service korzysta z istniejącej bazy PostgreSQL używanej przez RCP.
Tabele Pan Majster są izolowane w osobnym schemacie `panmajster`, więc nie
kolidują z tabelami RCP w schemacie domyślnym.
Zdjęcia, nagrania i PDF-y są tymczasowo przechowywane w tabeli
`panmajster.stored_blobs`. Ten wariant nie generuje nowych kosztów Rendera,
ale zużywa pojemność istniejącej bazy i służy wyłącznie do pilotażu.

W Render wybierz **New > Blueprint**, połącz repozytorium
`surawskip22/vmax-backend` i wskaż gałąź `pan-majster`. Przed pierwszym
wdrożeniem ustaw sekrety oznaczone w Blueprint jako `sync: false`:

- `OPENAI_API_KEY`,
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`,
- `DATABASE_URL` skopiowany z istniejącej usługi RCP,
- `ADMIN_EMAILS`, np. adres właściciela pilotażu.

Jeśli Render nada usłudze inny adres, zmień `APP_URL`. Start kontenera wykonuje
`alembic upgrade head`, a następnie uruchamia FastAPI i wbudowany worker.

## Dane i migracja zdjęć

Pliki otrzymują klucze `media/{project_id}/{asset_id}` i są zapisywane przez
warstwę storage. Na Renderze providerem jest `database`; lokalnie domyślnie
`local_disk`. Rekordy nadal korzystają z `storage_provider`, `storage_key`
i SHA-256, dlatego późniejsza migracja do magazynu obiektowego nie zmienia
modelu domenowego.

Eksport manifestu:

```powershell
python scripts/export_media_manifest.py --output media-manifest.json
```

Próbne kopiowanie i weryfikacja SHA-256:

```powershell
python scripts/migrate_media.py --target-dir media-export
```

Aktualizację dostawcy w bazie należy wykonać dopiero po dodaniu adaptera
docelowego magazynu i pełnym audycie kopii.
