# NEXT_STEPS_20260617 - Pan Majster

## Najblizszy kierunek

* Nie zaczynac nowych funkcji.
* Nie robic kolejnego cleanupu bez konkretnego powodu.
* Przygotowac projekt do testu zewnetrznego obecnej webowki.
* Jesli tester ma uzywac Rendera, najpierw swiadomie zrobic push + Render deploy.
* Po deployu zrobic krotki Render smoke.
* Zebrac feedback.
* Naprawiac tylko blockery i problemy krytyczne.
* Kazdy fix jako maly osobny commit.
* Po kazdym fixie odpalic testy/build adekwatne do zmiany.

## Opcje teraz

### A. Test zewnetrzny bez aktualizacji Rendera

Tylko jesli tester ma dostep do tej samej wersji, ktora chcemy sprawdzac.

### B. Swiadomy push + Render smoke

Jesli chcemy, zeby tester testowal aktualny local HEAD online.

### C. Maly fix

Tylko jesli smoke/tester pokaze blocker.

### D. Czekamy

Jesli nie ma testera i nie ma decyzji o deployu.

## Czego teraz nie robic

* nie zaczynac 5E,
* nie ruszac PDF,
* nie ruszac portfolio,
* nie ruszac platnosci,
* nie robic router split,
* nie ciac dalej `App.tsx` / `api.py`,
* nie robic Androida,
* nie robic dashboard/kafelkow,
* nie pushowac na Render bez decyzji,
* nie mieszac cleanupu z nowymi funkcjami.

## Kolejnosc pozniej

1. Test zewnetrzny obecnej webowki.
2. Fixy blockerow.
3. Web MVP hardening.
4. Android jako PWA/Capacitor wrapper.
5. Portfolio MVP pozniej.
6. PDF polish pozniej.
7. Osobna baza/storage pozniej.
8. Platnosci/faktury na koncu.
