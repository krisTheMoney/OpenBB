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

Actions er allerede slått på i dette repoet — workflows kjører på push.

1. **Slå sammen endringen til standardbranchen.** GitHub gjør ikke en workflow
   tilgjengelig for manuell kjøring før filen ligger på standardbranchen; før det svarer
   `workflow_dispatch` med 404. Den planlagte kjøringen krever det samme.
2. **Kjør workflowen én gang manuelt.** Actions → «📈 Daglig markedsoppdatering» → «Run
   workflow». Første kjøring oppretter `market-reports`-branchen og bekrefter samtidig at
   kildene faktisk svarer. Hele rapporten vises på sammendragssiden til kjøringen, så det
   er der man ser resultatet med en gang.
3. Etter det går den av seg selv hver hverdag.

## Claude-rutinen

Leveringslaget er satt opp som en Routine ved navn «Daglig markedsoppdatering», som fyrer
05:10 UTC mandag til fredag — tretti minutter etter workflowen — og sender pushvarsel.

Rutinen henter `reports/latest.json` med `curl` mot GitHub-API-et, ikke med
GitHub-MCP-verktøyene: sesjoner som fyres fra en Routine har ikke `mcp__*`-verktøy
tilgjengelig. Finner den ikke filen, sier den fra om at datalaget ikke har kjørt, i stedet
for å finne på tall.

Rutinen endres eller slås av fra Routines-oversikten på claude.ai.

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

Utledningen er deterministisk og gir samme svar hver kjøring. Rapporten oppgir de
utledede antallene i Forbehold-avsnittet.

Valutakursen som brukes er `snapshot_usdnok` i configen. Den er **kalibrert**, ikke hentet:
Yahoos sluttkurs for avlesningsdagen var 9,5042, og den ga en portefølje 0,67 % for lav
mot det megleren faktisk viste, fordi megleren veksler til sin egen kurs. Verdien i
configen er løst ut av meglerens rapporterte totalverdi og treffer den på øret. Utelates
linjen, faller koden tilbake på kursen kilden gir.

Dette er én kalibrert parameter for hele porteføljen, så den korrigerer et jevnt avvik,
ikke skjevheter i enkeltposisjoner. Har du de eksakte antallene, legg dem inn som `shares`
på hver posisjon — da brukes de direkte, utledningen hoppes over, og kalibreringen kan
fjernes.

## Hva rapporten inneholder

- **Porteføljen** — samlet verdi, dagens endring i kroner og prosent, delt i
  aksjebevegelse og valutaeffekt. Alle posisjonene er USD-noterte, så USDNOK slår rett
  inn på avkastningen i kroner; denne delingen viser hvor mye av dagen som var hva.
- **Posisjoner** — tabell sortert etter dagens bidrag i kroner.
- **Marked** — S&P 500, Nasdaq, Dow, VIX, amerikanske 5- og 10-årsrenter, USDNOK,
  EURNOK, Brent-olje og gull.
- **Selskapene** — analytikerkonsensus og kursmål, kommende resultatdato, utbytte og
  ferske nyheter per selskap. Hver nyhet får et kort sammendrag og en synlig lenke til
  kilden, slik at det går an å vurdere saken uten å åpne den.
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

Et forventet tomt svar er ikke en feil. Selskaper uten utbytte gir ingen utbyttedata, og
det logges ikke som en kildefeil — bare faktiske problemer havner under Forbehold.

Resultatkalenderen hentes fra Nasdaqs åpne endepunkt. Seeking Alpha står som reserve,
men svarte med en captcha-vegg da rapporten kjørte første gang, og er derfor ikke lenger
hovedkilden.

## Forbehold

Kursdataene kommer fra Yahoo Finance, som er et uoffisielt endepunkt uten garantier.
Kursene er sluttkurser, ikke sanntid.

Kostbasis er utledet fra skjermbildet av porteføljen, ikke fra faktiske handler.
Tallene stemmer med meglers P/L på avlesningstidspunktet. Regner megleren P/L i dollar
og konverterer, vil kroneavkastningen over tid kunne avvike noe fra appen; legg inn
faktisk GAV hvis det blir merkbart.

Rapporten er en datasammenstilling, ikke investeringsrådgivning.
