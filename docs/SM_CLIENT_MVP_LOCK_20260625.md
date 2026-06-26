# SM CLIENT MVP LOCK - 2026-06-25

## Status

Flow `samodzielny majster + link klienta /c` jest zamkniety jako stabilny zakres MVP po automatycznym sanity checku.

Manualny Render QA nadal jest wymagany przed oznaczeniem tego flow jako produkcyjnie sprawdzonego.

## Zakres zamknietego flow

- Tworzenie zlecenia przez samodzielnego majstra.
- Edycja zlecenia przez samodzielnego majstra.
- Statusy i etapy zlecenia, w tym oddzielne zarzadzanie etapem pracy.
- Dodawanie zdjec do postepu.
- Dodawanie audio do postepu.
- Spokojny fallback audio na mobile, gdy live transkrypcja nie jest dostepna.
- Zglaszanie problemow jako wpisow postepu.
- Obsluga problemow przez wykonawce lub szefa w panelu roboczym.
- Historia postepu z wpisami, mediami, problemami i komentarzami.
- Komentarze klienta w linku `/c`.
- Intencje klienta przy problemie: potwierdzenie rozwiazania, nadal otwarte, sugestia.
- Raporty PDF generowane z aktualnych danych.
- Publiczna nazwa wykonawcy w linku klienta `/c` i nowych PDF.
- Link klienta `/c` jako bezpieczny publiczny podglad bez panelowych uprawnien.
- Copy portfolio w formularzu zlecenia jako future-only po zakonczeniu zlecenia.
- Mobile overflow fix w szczegole zlecenia samodzielnego majstra.

## Poza zakresem locka

- Marketplace.
- Oferta wstepna / wycena.
- Umowy.
- Backendowe portfolio.
- Publiczny link portfolio cross-device.
- Wizytowka firmy.
- Rework szefa firmy.
- Rework inwestora.
- Rework `/g`.
- Transkrypcja backendowa audio.
- PWA / Capacitor.
- Platnosci.

## Znane ograniczenia

- Stare PDF-y sa snapshotami i nie zmieniaja publicznej nazwy wykonawcy po zmianie profilu.
- Live transkrypcja na Androidzie moze byc niedostepna; wtedy audio zapisuje sie bez transkrypcji.
- Portfolio jest nadal czesciowo frontend/localStorage MVP.
- `/g` wymaga przyszlego UI reworku przy pracach nad szefem firmy.
- Marketplace, oferta i umowy sa w roadmapie, ale nie w MVP locku.
- Manualny Render QA pozostaje wymagany.

## Ostatnie commity flow

- `037ee2c` - komentarze klienta do wpisow.
- `b10f72f` - spokojny fallback audio.
- `d80731c` - publiczna nazwa wykonawcy.
- `2b2ef0c` - portfolio copy jako future-only.
- `266515d` - checklista QA samodzielnego majstra i linku klienta.
- `83a9c12` - mobile overflow w szczegole zlecenia.

## Nastepne kroki po locku

1. Manual Render QA.
2. Bugfixy blockerow, jesli sa.
3. Finalny checkpoint.
4. Rework inwestora.
5. Rework szefa firmy.
6. `/g` UI rework.
7. Backend portfolio / publiczna wizytowka.
8. Oferta wstepna / wycena orientacyjna.
9. Umowy.
10. Marketplace.
