# PRODUCT_FLOW_NOTES

## Główna zasada produktu

Pan Majster to jedna aplikacja dla ekip, majstrów, szefów firm i prywatnych inwestorów.
Marka zawsze zostaje Pan Majster.

MVP nie ma być systemem do konfigurowania branż, presetów i pól specjalistycznych.
MVP ma być proste:

- zlecenie
- wykonawca
- zdjęcia
- nagranie głosowe albo notatka
- status
- link dla klienta
- raport

## Email i konta

- email nie może być wymagany przy dodawaniu majstra, ekipy, klienta ani inwestora
- email jest opcjonalny
- system musi rozróżniać profil stały / konto od linku jednorazowego
- majster / ekipa z linku ma od razu widzieć przypisane zlecenie bez emaila, OTP i logowania
- jeśli podany jest email, można utworzyć konto stałe
- jeśli podany jest email dla majstra / ekipy / klienta stałego, taka osoba powinna mieć używalne konto i dane logowania
- początkowy kod / hasło powinien działać jako login i potem dać się zmienić w ustawieniach

## Role i flow

### Szef firmy

- ma firmę / workspace
- może dodawać majstrów / ekipy
- może tworzyć zlecenia
- może przypisywać wykonawców
- może wysyłać link majstrowi / ekipie
- może wysyłać link klientowi do podglądu
- może tworzyć profil stały albo link jednorazowy
- widzi zespół i przypisane zlecenia

### Inwestor

- tworzy zlecenia
- dodaje wykonawców / majstrów / ekipy
- przypisuje wykonawcę do zlecenia
- nie tworzy własnej firmy wykonawczej
- przy swoim zleceniu nie powinien mieć linku klienta do samego siebie
- przy swoim zleceniu nie powinien być zmuszany do podawania emaila klienta
- powinien mieć link dla majstra / ekipy
- może tworzyć profil stały wykonawcy albo link jednorazowy

### Samodzielny majster

- jest firmą i wykonawcą w jednym
- nie dodaje kolejnych ekip
- tworzy swoje zlecenia
- wysyła linki klientom / zleceniodawcom
- może mieć stałych klientów albo linki jednorazowe
- może utworzyć klienta stałego z kontem albo wysłać jednorazowy link

### Majster / ekipa z linku

- nie musi mieć emaila
- nie musi mieć konta
- nie musi wpisywać OTP
- po kliknięciu linku od razu widzi konkretne zlecenie
- ma gotowy kontekst / profil pod linkiem
- może aktualizować tylko to jedno zlecenie zgodnie z uprawnieniami

### Klient / inwestor z linku

- dostaje podgląd konkretnego zlecenia
- widzi postęp, status, zdjęcia, raport i informacje przewidziane dla klienta
- nie widzi całej aplikacji
- może być klientem jednorazowym z linku albo klientem stałym z kontem

## Zlecenia

- statusy: Zlecone, W realizacji, Zakończono
- nowe zlecenie ma status Zlecone
- upload zdjęcia / wpisu z pracy przestawia zlecenie na W realizacji
- upload audio / nagrania głosowego też powinien być traktowany jako wpis z pracy, jeśli dotyczy flow pracy
- ma być przycisk Robota skończona / Zamknij zlecenie
- po zamknięciu status to Zakończono
- ma być możliwość ponownego otwarcia zlecenia, np. na poprawki
- edycja zlecenia
- usuwanie zlecenia z potwierdzeniem
- przypisywanie wykonawcy
- pole ma nazywać się Wykonawca, nie Firma
- szacunkowa data rozpoczęcia
- szacunkowa data zakończenia
- opcja +/- dla dat
- kwota umowna z dopiskiem, że może ulec zmianie i nie jest wiążącą kwotą końcową
- szef / inwestor / samodzielny majster mają mieć filtrowanie zleceń po: Zlecone, W realizacji, Zakończono

## Linki

- link dla majstra / ekipy służy do aktualizowania konkretnego zlecenia
- link klienta / inwestora służy do podglądu konkretnego zlecenia
- inwestor nie powinien dostawać linku klienta do samego siebie
- inwestor powinien widzieć link dla majstra / ekipy
- linki mają być proste i zrozumiałe
- link majstra / ekipy ma działać bez emaila, bez OTP i bez logowania

## Raporty i klient

- raport pokazuje godzinę publikacji
- raport pokazuje kwotę umowną, jeśli została wpisana
- raport może zawierać notatkę szefa / samodzielnego majstra
- raport może pokazywać transkrypcję audio, jeśli jest wygenerowana i jeśli to pasuje do publicznego widoku
- klient z linku może komentować problem
- klient z linku może oznaczyć problem jako rozwiązany / zaakceptowany
- po zakończeniu klient może dodać komentarz / opinię
- w przyszłości, jeśli właściciel doda zlecenie do portfolio, komentarz i ocena klienta / inwestora mogą być pokazane przy realizacji
- płatności karta / BLIK / przelew są później, nie teraz

## Tryb prosty

- tryb prosty nie ma być pytaniem na pierwszym wejściu
- tryb prosty nie ma być schowany tylko w ustawieniach
- ma być przełącznikiem u góry widoku
- ma umożliwiać szybki flow: zdjęcie, zdjęcie, nagranie głosowe, zapis
- samodzielny majster ma móc zmieniać tryb wyświetlania kiedy chce

## Landing page

- usunąć napisy LOGO GŁÓWNE i IKONA APLIKACJI
- tekst: Dla ekip. Dla majstrów. Dla szefów firm i prywatnych inwestorów.
- tekst: Zdjęcie. Nagranie głosowe. Raport. Podgląd.

## Audio i transkrypcja

- audio jest jedną z głównych funkcji MVP
- w KROKU 2 robimy tylko audyt audio / transkrypcji / playera
- pełna naprawa audio jest planowana w KROKU 6
- jeżeli audyt pokaże, że audio jest fundamentalnie rozwalone albo brakuje playera w widoku szefa mimo istniejącego pliku, robimy wcześniej KROK 2.5 audio hotfix
- sprawdzić bug, że po nagraniu notatki głosowej szef jej nie słyszy
- sprawdzić zapis nagrania
- sprawdzić upload
- sprawdzić MIME / URL
- sprawdzić uprawnienia
- sprawdzić odtwarzanie jako szef
- sprawdzić, czy majster z linku może nagrać audio
- sprawdzić, czy szef słyszy audio nagrane przez majstra z linku
- sprawdzić, czy klient z linku widzi tylko to, co powinien
- sprawdzić, czy istnieje transkrypcja audio
- sprawdzić, czy transkrypcja jest powiązana z oryginalnym plikiem audio
- sprawdzić, czy widok szefa pokazuje player audio, a nie tylko tekst transkrypcji
- sprawdzić, czy frontend nie myli audio file z transcript text
- sprawdzić, czy problem nie wynika po prostu z mikrofonu na urządzeniu testowym

## Presety branżowe - później, nie MVP

- presety branżowe nie są częścią MVP
- custom fields nie są częścią MVP
- w przyszłości można dodać proste typy działalności / presety formularza zlecenia
- na tym etapie nie robimy listy branż, konfiguratora formularzy ani pól specjalistycznych
- MVP ma pozostać uniwersalne i proste

## Czego teraz nie robić

- nie robić osobnych aplikacji per branża
- nie robić osobnych modeli typu AutoRepair, PetVisit, InsuranceClaim
- nie wdrażać custom fields w MVP
- nie wdrażać presetów branżowych w MVP
- nie przechowywać haseł CCTV
- nie wdrażać płatności, faktur ani księgowości
- nie wdrażać pełnego white labelu
- nie robić checklist przed ustabilizowaniem core flow

## Rekomendowana kolejność implementacji

1. lokalna stabilizacja
2. audyt obecnego stanu + audyt audio / transkrypcji / playera + PRODUCT_FLOW_NOTES.md
3. opcjonalnie audio hotfix, tylko jeśli audio jest fundamentalnie rozwalone albo brakuje prostego playera w istniejącym widoku
4. email opcjonalny i link majstra bez logowania
5. role i flow
6. statusy i zarządzanie zleceniami
7. raporty, komentarze, problemy i pełny fix audio / transkrypcji / playera
8. landing page i teksty
9. presety branżowe / custom fields dopiero później, jeśli MVP będzie działać prosto
