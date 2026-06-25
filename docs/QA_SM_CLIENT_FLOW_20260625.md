# QA SM / Client Flow - Render Checklist

Krótka checklista manualnego sprawdzenia po zmianach: komentarze klienta, audio fallback, publiczna nazwa wykonawcy i copy portfolio w formularzu zlecenia.

## 1. Deploy / Migracje

- [ ] Render deploy przeszedł bez błędu.
- [ ] Migracja `20260625_11_comment_intent_author_type.py` jest na bazie Render.
- [ ] Migracja `20260625_12_user_public_profile_name.py` jest na bazie Render.
- [ ] `/api/health` odpowiada po deployu.

## 2. Samodzielny majster - Ustawienia

- [ ] Zaloguj jako `samodzielny@majster.pl`.
- [ ] W `Ustawienia` zmień `Nazwa profilu`.
- [ ] Zapis działa bez odświeżania F5.
- [ ] Nowa nazwa jest widoczna w linku klienta `/c/...`.
- [ ] Nowy PDF pokazuje nową publiczną nazwę wykonawcy.

## 3. Samodzielny majster - Zlecenie

- [ ] Dodaj nowe zlecenie.
- [ ] Edytuj istniejące zlecenie.
- [ ] Sekcja portfolio mówi, że realizację można dodać po zakończeniu do `Mojej wizytówki`.
- [ ] Formularz nie sugeruje automatycznej publikacji zlecenia.
- [ ] Brak aktywnego checkboxa `Pokaż realizację w publicznym portfolio`.

## 4. Dodaj Postęp

- [ ] Dodanie zdjęć działa.
- [ ] Dodanie audio działa.
- [ ] Dodanie wpisu tekstowego działa.
- [ ] Dodanie problemu działa.
- [ ] Status problemu można obsłużyć zgodnie z uprawnieniami.

## 5. Audio

- [ ] PC: live transcription może działać, jeśli przeglądarka wspiera Web Speech API.
- [ ] Android: brak straszącego błędu, gdy live transcription jest niedostępna.
- [ ] Android: nagranie audio zapisuje się także bez transkrypcji.
- [ ] Prawdziwy błąd mikrofonu nadal pokazuje komunikat o uprawnieniach mikrofonu.

## 6. Link Klienta `/c/...`

- [ ] Historia wpisów jest chronologiczna i czytelna.
- [ ] Zdjęcia są widoczne.
- [ ] Audio jest widoczne/odtwarzalne.
- [ ] Klient może dodać komentarz do wpisu.
- [ ] Przy wpisie typu problem klient widzi intencje problemu.
- [ ] Publiczna nazwa wykonawcy jest poprawna.

## 7. PDF

- [ ] Nowo wygenerowany PDF ma publiczną nazwę wykonawcy.
- [ ] Stare PDF-y traktujemy jako snapshoty i nie oczekujemy zmiany nazwy wstecz.
- [ ] Generowanie PDF nie zawiesza widoku i nie otwiera 502 w tej samej karcie aplikacji.

## 8. Uprawnienia

- [ ] Klient `/c/...` nie może używać panelowych PATCH/DELETE.
- [ ] `/g/...` link-only nie dostał przypadkiem funkcji klienta.
- [ ] `company_worker` nadal widzi tylko swoje flow robocze.
- [ ] Inwestor i szef nie dostali mylącej opcji portfolio w formularzu zlecenia.

## 9. Znane Ograniczenia

- Stare PDF-y są snapshotami i nie zmieniają nazwy wykonawcy po edycji profilu.
- Live transcription na Androidzie może być niedostępna; audio ma się wtedy zapisać bez transkrypcji.
- Portfolio samodzielnego majstra jest nadal częściowo frontend/localStorage MVP.
- Marketplace, oferty i umowy są w roadmapie, nie w tym checku.
