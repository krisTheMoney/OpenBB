# Daglig markeds- og porteføljeoppdatering

Genererer hver morgen en rapport med hvordan markedet beveget seg forrige handelsdag,
og hva som er verdt å vite om hvert selskap i porteføljen. Bygget på OpenBB-providerne
i dette repoet. Alle datakilder er gratis og krever ingen API-nøkkel.

## Slik henger det sammen

Oppdateringen kjøres i to lag.

**Datalaget** er GitHub Actions-workflowen `.github/workflows/daily-market-brief.yml`.
Den kjører 04:40 UTC mandag til fredag, henter tallene og publiserer
`reports/YYYY-MM-DD.md` og `reports/latest.json` til branchen `market-reports`.
Rapportbranchen er foreldreløs og inneholder bare rapporter, aldri kode, slik at
kodebranchene holdes rene.

**Leveringslaget** er en Claude Routine som leser `reports/latest.json` og presenterer
innholdet med nyhets- og makrokontekst. Leveringslaget regner aldri egne tall — alt som
er tall kommer fra JSON-filen datalaget skrev.

Grunnen til delingen er at utviklingsmiljøet til Claude ikke har nett-tilgang til
markedsdata; egress-policyen blokkerer Yahoo Finance og alle tilsvarende kilder.
GitHub-runnerne har full tilgang, så det er der tallene hentes.

## Oppsett

1. **Slå på Actions.** Forks har workflows deaktivert som standard. Gå til Actions-fanen
   i repoet og trykk «I understand my workflows, enable them». Uten dette kjører
   ingenting.
2. **Kjør workflowen én gang manuelt.** Actions → «📈 Daglig markedsoppdatering» → «Run
   workflow». Første kjøring oppretter `market-reports`-branchen.
3. Ferdig. Rapporten legges ut hver morgen, og hele rapporten vises også på
   sammendragssiden til hver kjøring.

## Porteføljen

Posisjonene ligger i [`portfolio.toml`](portfolio.toml). Etter enhver endring:

```bash
python run_brief.py --validate
```

Kommandoen sjekker at posisjonene pluss kontanter summerer til `reported_total_nok` —
totalen megler-appen viste da posisjonene ble lest av. Det fanger en feillest linje før
den rekker å bli til feil tall i en rapport. Toleransen er én krone, som dekker
avrunding i appen.

### Oppdatering ved handel

Send Claude et nytt skjermbilde av porteføljen og be om at `portfolio.toml` oppdateres.
Filen kan også redigeres for hånd i GitHub-webeditoren. Etter endring bør `--validate`
kjøres, enten lokalt eller ved å starte workflowen manuelt — den validerer før den
henter data.

### Hvorfor antall aksjer utledes

Megler-appen viser verdi i kroner og kurs i dollar, men ikke antall aksjer, og
posisjonene er fraksjonelle. Antallet regnes derfor ut som

```
antall = verdi_nok / (kurs_usd × USDNOK på avlesningsdagen)
```

USDNOK på `snapshot_date` hentes historisk ved hver kjøring, så utledningen er
deterministisk og gir samme svar hver gang. Rapporten oppgir de utledede antallene i
Forbehold-avsnittet.

Har du de eksakte antallene, legg dem inn som `shares` på posisjonen. Da brukes de
direkte og utledningen hoppes over.

## Hva rapporten inneholder

- **Porteføljen** — samlet verdi, dagens endring i kroner og prosent, delt i
  aksjebevegelse og valutaeffekt. Alle posisjonene er USD-noterte, så USDNOK slår rett
  inn på avkastningen i kroner; denne delingen viser hvor mye av dagen som var hva.
- **Posisjoner** — tabell sortert etter dagens bidrag i kroner.
- **Marked** — S&P 500, Nasdaq, Dow, VIX, amerikanske 5- og 10-årsrenter, USDNOK,
  EURNOK, Brent-olje og gull.
- **Selskapene** — analytikerkonsensus og kursmål, kommende resultatdato, utbytte og
  ferske nyheter per selskap.
- **Kalender** — hendelser i porteføljen de neste fjorten dagene.
- **Forbehold** — utledede antall aksjer, og hvilke kilder som eventuelt ikke svarte.

## Kjøre lokalt

```bash
pip install -r requirements.txt
python run_brief.py                  # henter ferske data
python run_brief.py --offline        # bygger fra lagrede testdata, uten nett
python run_brief.py --validate       # kontrollerer bare porteføljen
python run_brief.py --out ./ut       # velger utmappe
```

## Tester

```bash
python -m pytest tests/
```

Testene bruker lagrede API-svar i `tests/fixtures/offline_bundle.json` og går uten
nettverk. De dekker regningen som faktisk kan bli feil: utledning av antall aksjer,
dekomponering av avkastning i aksje- og valutabidrag, validering av porteføljen og
rendering av rapporten.

Datoene i testdataene er relative (`hours_ago`, `days_ahead`), slik at bundelen ikke
blir foreldet og nyhetsfiltreringen testes slik den faktisk oppfører seg.

## Når noe feiler

Hver kilde er isolert. Faller nyhetene bort eller svarer ikke resultatkalenderen,
fortsetter resten, og det som manglet listes under Forbehold i rapporten. En posisjon
uten kursdata tas ut av summene og nevnes eksplisitt framfor å telle som null.

Det ene unntaket er USDNOK. Hele porteføljen er notert i USD, så uten valutakursen
finnes det ingen kronetall å rapportere. Da stopper kjøringen med en tydelig feil i
stedet for å publisere en rapport som ser komplett ut, men mangler poenget.

## Forbehold

Kursdataene kommer fra Yahoo Finance, som er et uoffisielt endepunkt uten garantier.
Kursene er sluttkurser, ikke sanntid.

Kostbasis er utledet fra skjermbildet av porteføljen, ikke fra faktiske handler.
Tallene stemmer med meglers P/L på avlesningstidspunktet. Regner megleren P/L i dollar
og konverterer, vil kroneavkastningen over tid kunne avvike noe fra appen; legg inn
faktisk GAV hvis det blir merkbart.

Rapporten er en datasammenstilling, ikke investeringsrådgivning.
