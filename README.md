# rpa-befordring-kontrol

Kontrollerer at ansøgninger om skolekørsel fra OS2Forms er blevet til bevillinger
i Befordringssystemet — og opretter dem, der mangler.

## Hvorfor

En borger ansøger om befordring gennem en af tre OS2Forms-formularer. To ting
skal derefter ske, og de er uafhængige af hinanden:

- **Journalisering** — formularens dokumenter lægges i GetOrganized.
  `go_journalisering` klarer det.
- **Bevilling** — ansøgningen skal frem til en sagsbehandler i
  Befordringssystemet. Det er denne proces.

Det blev tidligere gjort af en *remote post handler* på selve OS2Forms-formularen,
som kaldte Befordringssystemet direkte. Det virker, men der er intet, der opdager
det, hvis kaldet fejler: ansøgningen findes, dokumenterne er journaliseret, og
ingen bevilling dukker op.

Denne proces spørger i stedet den anden vej: *hvilke ansøgninger er kommet ind,
og mangler nogen af dem en bevilling?* Den finder huller uanset årsag — et fejlet
kald, en nedlukning midt i det hele, en fejl i feltmapningen der siden er rettet.

## Kontroller

Processen kører flere kontroller på samme workqueue. Hver kontrol lægger emner
op med sit eget præfiks i referencen, og `process_item` fordeler på typen:

| Type | Reference | Hvad den gør |
| --- | --- | --- |
| `opret` | `opret:<form_id>` | Indsendelser til de tre kørselsformularer, der mangler en bevilling. Kalder `POST /os2forms/create_bevilling/{cpr}`. |
| `esdh` | `esdh:<bevilling_id>` | Bevillinger uden `esdh_noegle`. Finder barnets befordringssag i GO og skriver både sags-id og link tilbage. |

To faser, som alle ATS-processer: `--queue` finder arbejdet, `--process` udfører det.

### `esdh`-kontrollen

Bevillinger oprettet ud fra en OS2Forms-indsendelse har ingen ESDH-nøgle —
indsendelsen kender ikke GO-sagen — og feltet kan ikke sættes i brugerfladen.
Nattekørslen udleder `esdh_url` *ud fra* nøglen, så uden den får bevillingen
heller aldrig et link til sagen.

Kontrollen slår op i GO: barnet findes på CPR, og derfra findes sagen med
titlen *"Kørsel til …"*.

**Opslaget er udelukkende læsende.** `go_journalisering` opretter sagerne —
dens PPR-flow er find-eller-opret. Hvis denne proces også oprettede, ville en
bevilling, hvis indsendelse endnu ikke er journaliseret, ende med to PPR-sager
for samme barn. Findes der ingen sag, er svaret her "ikke endnu".

Både nøglen og linket skrives, i samme kald. Linket er det, en sagsbehandler
rent faktisk klikker på, og det kan ikke sammensættes af nøglen — GO's URL
indeholder et sags-id (`PPR01`), som ikke findes i befordringsdatabasen. Derfor
slås det op på GO's **API-vært** og bygges af **browser-værten**; de er ikke den
samme, og et link bygget af API-værten peger et sted, sagsbehandleren ikke har
adgang til.

Kan linket ikke udledes, skrives nøglen alligevel. Nattekørslens trin 8 er
stadig bagstopper og prøver linket igen — et manglende link er kosmetisk, en
manglende nøgle betyder, at bevillingen slet ikke har en sag.

### Gentagelse

De to kontroller har modsatte regler:

- Et `opret`-emne forsøges **én gang**. En fejl betyder, at indsendelsen kræver
  et menneske — ingen CPR, en adresse der ikke matcher — og at lægge den i kø
  hver halve time ville blot gentage fejlen.
- Et `esdh`-emne forsøges **igen**. "Ikke endnu" er det normale første svar:
  en bevilling kan findes, før dens indsendelse er journaliseret, og dermed før
  GO-sagen findes.

`queue_handler.genaktiver_ventende` sætter ventende og fejlede `esdh`-emner
tilbage til status `new` ved starten af hver `--queue`-kørsel. Emnet
genbruges altså — der oprettes ikke et nyt.

Det er med vilje. `--process` henter arbejde gennem
`GET /workqueues/{id}/next_item`, som kun udleverer emner, ATS stadig regner for
udestående; et emne i `pending user action` ligger bevidst uden for den mængde.
Alternativet — at tilføje et nyt emne med samme reference — virker også, men
efterlader det gamle for altid: en bevilling, der først går igennem i tredje
forsøg, ville stå med to permanente `pending user action`-rækker ved siden af
den, der lykkedes, og kun den sidste ville være sand.

### Afhængigheden til journaliseringen

Processen læser **kun** `view_Journalizing` — en publiceret view, ikke
journaliseringens egne tabeller. Tabellerne bagved indeholder journaliseringens
tilstandsmaskine (`status`, `attempt_count`, response-JSON), og den er deres
interne anliggende.

`status` filtreres der **bevidst ikke** på:

- En bevilling afhænger ikke af, at journaliseringen lykkedes. Det er to
  forskellige forpligtelser — bevillingen er borgerens ansøgning, journaliseringen
  er en arkiveringspligt.
- Et filter på `'Successful'` ville betyde, at en fejl i journaliseringen stille
  og roligt holdt borgeres ansøgninger væk fra sagsbehandlerne — usynligt fra
  begge sider.
- Det ville desuden binde processen til deres statusnavne.

Der filtreres til gengæld på, at status **ikke** er `Manual`. Det er en anden
slags filter: alle indsendelser fra før denne proces fandtes har den status, så
det er et ekstra net under `TIDLIGSTE_FORMULAR_DATO`.

Retningen er det afgørende. Filteret *fravælger* en status i stedet for at
*kræve* en — hvis journaliseringen omdøber en status eller tilføjer en ny, holder
filteret op med at fravælge, og kørslen ser *flere* rækker, som skæringsdatoen og
`os2forms_id`-spærringen så fanger. Et krav om `'Successful'` ville fejle den
anden vej og tabe en borgers ansøgning, uden at nogen opdagede det.

`service-tandplejen-procesoverblik` læser samme view på samme måde.

## Idempotens

Den samme indsendelse kan ses mange gange. Tre lag sikrer én bevilling pr.
ansøgning:

0. Formularer med status `Manual` — alt fra før processen — læses slet ikke.
1. `populate_queue` springer referencer over, der allerede ligger i workqueuen.
2. Befordringssystemet afviser en bevilling med et `os2forms_id`, der findes i
   forvejen, og svarer `already_exists`.
3. Et unikt indeks på `Bevilling.os2forms_id` håndhæver det i databasen, også
   hvis to kørsler overlapper.

## Opsætning

### Miljøvariabler

Se `.env.example`. Ud over ATS' egne:

| Variabel | Bruges til |
| --- | --- |
| `DBConnectionString` | RPA-databasen, hvor `view_Journalizing` læses. |
| (GO-legitimation) | Hentes via `RPAConnection` — konstanten `go_api_endpoint` og credential'et `go_api`. Ikke en miljøvariabel. |
| `BEFORDRING_API_ENDPOINT` | Befordringssystemets API, inkl. `/api`. |
| `BEFORDRING_API_KEY` | Sendes som `X-API-Key`. |

### `TIDLIGSTE_FORMULAR_DATO`

**Den vigtigste indstilling i `ats_framework/helpers/config.py`.**

`view_Journalizing` indeholder hver eneste indsendelse til de tre formularer,
også fra før denne proces fandtes. Uden en skæringsdato opretter første kørsel en
bevilling for dem alle.

Sæt den til det tidspunkt, hvor OS2Forms' remote post handler **slukkes**. Før det
tidspunkt opretter OS2Forms selv bevillingen, og de rækker har ingen
`os2forms_id` — handleren sender ikke noget — så dubletspærringen i
Befordringssystemet kan ikke genkende dem, og processen ville oprette en bevilling
nummer to for hver ansøgning, der allerede er håndteret.

### Rækkefølge ved idriftsættelse

1. Kør migration `028_add_bevilling_os2forms_id.sql` i Befordringssystemet.
2. Deploy Befordringssystemets backend (dubletspærringen).
3. Sæt `TIDLIGSTE_FORMULAR_DATO` til i dag.
4. Sluk de tre remote post handlers i OS2Forms.
5. Start denne proces.

Punkt 3 og 4 hører sammen — mellem dem oprettes ingen bevillinger automatisk.

## Udvikling

```sh
uv venv .venv
uv sync
.venv/bin/ruff check .
```

`pyodbc` kræver `unixodbc` og Microsofts `msodbcsql18` for at kunne importeres på
Linux.

## Hvad processen kan udvides med

Formen — "sammenlign to billeder af verden, og ret forskellen" — passer på mere
end bevillingsoprettelse. Oplagte næste kontroller:

- Bevillinger uden `matrikel_id`.
- Elever uden beregnet `skoleafstand`.
- Bevillinger hvor skolekoden ikke længere matcher elevens.
