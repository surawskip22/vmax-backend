# Pan Majster

Robocze srodowisko nowej aplikacji, utrzymywane na osobnej galezi tego samego
repozytorium co rejestrator czasu pracy.

## Lokalnie

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

Aplikacja: http://localhost:8000

Kontrola stanu: http://localhost:8000/health

Dokumentacja API: http://localhost:8000/docs

## Render

Plik `render.yaml` definiuje osobny Web Service `pan-majster`, wdrazany z galezi
`pan-majster`. Nie zmienia ani nie zastepuje serwisu rejestratora czasu pracy,
ktory nadal korzysta z galezi `main`.

W panelu Render wybierz **New > Blueprint**, podlacz repozytorium
`surawskip22/vmax-backend` i zatwierdz usluge wykryta z `render.yaml`.

Docelowe sekrety i polaczenie z baza danych nalezy dodawac jako zmienne
srodowiskowe Render, bez umieszczania ich w repozytorium.
