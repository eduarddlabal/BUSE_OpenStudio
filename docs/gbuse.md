# Modul gBUSE v BUSE Studiu

Modul **gBUSE** nahrazuje původní editory databází informačních panelů BUSE – jak **gBUSE1** (vnitřní jednořádkové LED panely BS120 aj.), tak **gBUSE0** (vnější DOT-LED / LED / zářivkové panely BS110, BS210, BS310 aj.). Umožňuje číst, upravovat, kontrolovat, vizuálně simulovat a bezeztrátově sestavovat data panelů mezi binárním obrazem / Intel HEX a čitelnými soubory YAML.

Nízkoúrovňový popis binárního formátu, hlaviček, ukazatelů a IBIS povelů je v [docs/PROTOKOL.md](PROTOKOL.md).

---

## 1. Přehled a architektura

### Tok dat

```text
  .hex / .bin (FLASH panelu)
        │  Project.from_hex() / Project.from_bin() / Project.from_bytes()
        ▼
  Image (gBUSE1: 0xAA / 0xAB)   nebo   Database0 (gBUSE0: 0x55 / 0x57)
        │  Project.from_image()       │  Project.from_gbuse0()
        ▼                             ▼
        └──────────────┬──────────────┘
                       ▼
          Project (projekt.yaml + fonts.yaml)
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
    Web UI         CLI nástroje    HTTP API
 (gbuse.js)         (cli.py)       (app.py)
        │              │              │
        └──────────────┬──────────────┘
                       ▼
          to_image() / to_gbuse0() ──► .hex / .bin / Loader (nahrání do panelu)
```

### Rozdělení souborů v repozitáři

| Vrstva | Soubor | Účel |
|---|---|---|
| Vícepanelová sada | [`busepanel/core/bundle.py`](../busepanel/core/bundle.py) | `ProjectBundle` (`busestudio.project/v2`), správa sady panelů gBUSE0+gBUSE1, rozkopírování záznamů s autoformátováním, hromadný ZIP export, vestavěný ukázkový a prázdný projekt. |
| Projekt | [`busepanel/core/project.py`](../busepanel/core/project.py) | Jednotná třída `Project` (`format`: `gbuse0` \| `gbuse1`), načítání/ukládání YAML, převod z/do HEX/BIN, validace `check()`, šablony `new_gbuse0` / `new_gbuse1`. |
| Binární obraz gBUSE1 | [`busepanel/core/image.py`](../busepanel/core/image.py) | Parser a builder gBUSE1 pro FW `1.xx` (`0xAA`) a `2.x` (`0xAB`), bloky `STR`, `KOD`, `CYK`, `DOP`, `FNT`, `MIM`, `LIN`, `CIL`, `CIL4`, `ZST`, metadata `CYK_SOURCES`/`CYK_ENTER`/`CYK_LEAVE`. |
| Texty a značky gBUSE1 | [`busepanel/core/text.py`](../busepanel/core/text.py) | Překlad strukturovaných objektů / značek `{f3}{t2}{blink}{z:15}` a gBUSE1 MNEMO (`to_mnemo1`/`from_mnemo1`), automatické formátování `autoformat_gbuse1`. |
| Fonty a kódová tabulka | [`busepanel/core/fonts.py`](../busepanel/core/fonts.py) | Znaky proměnné výšky `1–28` bodů (`Glyph`), 160B kódová tabulka (`0x21–0xAF` + `0xF0–0xFF` + `0xFF`), Správce fontů (`copy_glyph`, `copy_font`, `delete_*`), soubory `.fnt`. |
| Vykreslování gBUSE1 | [`busepanel/core/render.py`](../busepanel/core/render.py) | Výpočet bitmapy záznamu, vícefázové stránkování `render_frames`, simulátor kroků cyklu `simulate_cycle_step`, složení pro terminál/PNG (`compose`). |
| Binární obraz gBUSE0 | [`busepanel/gbuse0/database.py`](../busepanel/gbuse0/database.py) | Parser a builder gBUSE0 (`0x55` FW 1.6 i `0x57` FW 2.11–3.18), 24bitový ukazatel `DRU`, masky povelů, příznaky `P1–P5`, `AFC`/`AFD`/`KOD`/`STR`, kontroly pro BS110 (`bs110_warnings`). |
| Program a náhled gBUSE0 | [`busepanel/gbuse0/program.py`](../busepanel/gbuse0/program.py) | Interpret řídicích kódů v záznamu (`Layer`, `ESC l/p/h/d/s/c/i/o/x/n/r/b/w/z`, `FF/LF/VT/CR`), kombinované vykreslení `render_combined` a export ASCII/PNG. |
| MNEMO a skladba gBUSE0 | [`busepanel/gbuse0/mnemo.py`](../busepanel/gbuse0/mnemo.py) | Kompilátor/dekompilátor oficiálního MNEMO (`to_mnemo`/`from_mnemo`), automatické formátování přes `AFC`/`AFD` (`autoformat_line`), skládání 1řádkových/2řádkových/střídaných záznamů (`compose_record`/`decompose_record`). |
| Pomocné formáty | [`busepanel/gbuse0/formats.py`](../busepanel/gbuse0/formats.py) | Sekční soubory (`.lin`, `.cil`, `.ci4`, `.dru`, `.zst`, `.mim`, `.dop`, `.kod`, `.aL`/`.LA`, `.afm`/`.afc`/`.afd`), `.txt` (`$` šablony i DOS `@` Kamenických) a ekvivalenty BS100/BS230 (`.bbd`/`.bbf`). |
| Hromadný import tabulek | [`busepanel/core/importers.py`](../busepanel/core/importers.py) | Načtení záznamů z CSV a XLSX do projektu. |
| Server a Web UI | [`busepanel/server/app.py`](../busepanel/server/app.py) (`*_api.py`), [`busepanel/web/gbuse/`](../busepanel/web/gbuse/gbuse.js) | Modulární FastAPI routery (`projects_api`, `bundle_api`, `editor_api`, `fonts_api`, `hardware_api`, `live_api`, `sim_api`) pod `/api/*` a nativní ES moduly prohlížečového editoru. |
| Příkazová řádka | [`busepanel/cli.py`](../busepanel/cli.py) | Příkazy `new`, `import-hex`, `export-hex`, `check`, `render`, `import`, `upload`, `backup`. |

---

## 2. Jednopanelové i vícepanelové projekty (`ProjectBundle`)

Program podporuje jak **jednopanelové projekty** (otevření `.hex`/`.bin`/`.yaml` konkrétního panelu), tak **vícepanelové sady projektů** (`schema: busestudio.project/v2`), které sdružují např. čelní + boční panel `gBUSE0` i vnitřní jednořádek `gBUSE1` dohromady:

```text
projekty/<nazev>/
├── projekt.yaml          # manifest sady (meta, seznam panelů, volitelně simulace a živě)
├── panely/
│   ├── celni/            # gBUSE0 140×19 (projekt.yaml + fonts.yaml)
│   ├── bocni/            # gBUSE0 112×19 + DRU
│   └── vnitrni/          # gBUSE1 135×8 (CYK + DOP)
└── zive.yaml             # (volitelně) uložené předvolby ručních textů pro tento projekt
```

- **Zabudované výchozí sady** (bez cizích dat dopravců):
  - **Ukázkový autobus (`demo_bus`)**: čelní `140×19` + boční s nácestnými `112×19` + vnitřní `135×8` typ 2 s ukázkovými linkami, cíli, zastávkami, mimořádností, předvolbami živého režimu a vazbou na virtuální vozidlo.
  - **Prázdný projekt (`empty_bus_3` / `empty_bus_4` / jednopanelový)**: čistá konfigurace pouze s originálními fonty.
- **Rozkopírování záznamu na ostatní panely (`⇄`)**: v editoru *Texty* lze jedním tlačítkem zkopírovat upravovaný záznam na ostatní panely sady – každý cílový panel si jej sám naformátuje (gBUSE0 přes své `AFC`/`AFD` a rozměry oken, gBUSE1 přes `autoformat_gbuse1` a případné zapnutí běžícího textu `t1`; zadní panel `rear` přebírá jen linky).
- **Společný náhled a ZIP export**: přepínač *Zobrazit všechny panely* vykreslí všechny panely soupravy najednou nad stejnou linkou/cílem/zastávkou; tlačítko *ZIP (vše)* vyexportuje `<id>.hex` a `<id>.bin` pro každý panel, společné `ekvivalenty.bbd` i kontrolní `manifest.txt`.
- **Sdílené šablony a propojení se simulací**: globální šablony zůstávají v pracovních složkách `systemy/`, `vozidla/`, `trasy/` a `zive/` a jsou volitelné – databáze gBUSE funguje plně i bez simulátoru. Simulace (karta *Vozidla → Univerzální projekt pro simulaci* i příkaz `busepanel quickstart`) umí sestavit nebo otevřít vícepanelový projekt pod `projekty/` (např. `projekty/univerzalni/projekt.yaml` se zpětnou podporou starších databází v `databaze/`) a automaticky odvodit konfiguraci panelů vozidla včetně napojení vnějších `gBUSE0` panelů (`.yaml`, `.hex`, `.bin` i sady `ProjectBundle`).

U jednopanelového projektu určuje rodinu klíč `panel.format` (`gbuse0` nebo `gbuse1`); při otevření `.hex`/`.bin` ji program pozná podle prvního bajtu (`0x57`/`0x55` = `gBUSE0`, `0xAB`/`0xAA` = `gBUSE1`). Kliknutím na **+ Přidat panel** lze kdykoliv i jednopanelový projekt rozšířit na vícepanelovou sadu.

```yaml
# Příklad hlavičky gBUSE0
panel: {format: gbuse0, type: BS210, ibis_address: 10, panel_number: 0, brightness: 0}
config:
  fw: "3.08"
  columns: 112
  rows: 19
  commands: [l, z, zI, aV, aA, zD, zN]
  properties: [flip_all]
  fields:
    line: {r0: 1, r1: 19, c0: 1, c1: 28, font: 8, spacing: 1, shift: 0, interval: 1.0}
    dest: {r0: 9, r1: 19, c0: 29, c1: 112, font: 3, spacing: 1, shift: 0, interval: 1.0}
    stop: {r0: 1, r1: 8, c0: 29, c1: 112, font: 11, spacing: 1, shift: 0, interval: 3.0}
fonts: fonts.yaml
lines:
  "001": "f8 m1 '1' CR"
destinations:
  "001": "f3 m1 'HLAVNÍ NÁDRAŽÍ' CR"
stops:
  "0001": "f11 m1 'Náměstí' CR"
```

Duplicitní čísla záznamů v původních datech (pokud se vyskytují) jsou v YAML rozlišena příponou ` (2)` tak, aby se při zpětném uložení do `.hex`/`.bin` neztratil žádný bajt.

---

## 3. Webové rozhraní modulu gBUSE

Ve webové aplikaci (`busepanel serve`) přepíná modul **gBUSE → Databáze** své prvky podle toho, zda je otevřen projekt **gBUSE1** nebo **gBUSE0**.

### Horní lišta a adaptivní LED náhled
- **Práce s projektem**: tlačítko **Projekty** přepne na přehled všech databází, rozbalovací seznam umožňuje rychlé přepnutí podle názvu a rozměru (`[GBUSE0 140×19] ...`), tlačítka `Nový…`, `Uložit` (`Ctrl+S`), `Uložit jako…`, `HEX` a `BIN`. Indikátor `● neuloženo` upozorňuje na neuložené změny.
- **Plátno LED náhledu (`#led`)**:
  - **gBUSE1**: kreslí 8řádkový červenooranžový LED panel, plynule animuje běžící text (`t1`), posuv zprava (`t2`), nahrnutí zleva (`t3`), blikání (`b`) i více stránek (`LF`/`VT`). Pro `typ panelu 2` umožňuje horní lišta přepnout z náhledu textů na **Cyklus (typ 2)** a krokovat nebo plynule přehrávat jednotlivé kroky cyklu se simulací volného textu, času a tarifního pásma.
  - **gBUSE0**: kreslí maticový žlutý DOT-LED panel libovolných rozměrů (např. `140×19`, `112×19`, `28×19`), skládá do jednoho obrazu vybranou **linku**, **cíl** i **nácestnou zastávku**, přehrává střídání stránek (`LF`/`VT`) a hlásí přetečení každého pole samostatně.

### Záložka Projekty
- **Karty projektů**: přehled všech `.yaml`, `.hex` i `.bin` databází v pracovní složce včetně názvu projektu, popisu, formátu (`gBUSE0` / `gBUSE1`), rozměrů, verze FW, počtů záznamů (`linek · cílů · zastávek…`), relativní cesty a data změny.
- **Akce přímo u karty**: `Otevřít` (rovnou přepne na editor *Texty*), `Duplikovat` (vytvoří kopii do nové složky v `projekty/`) a `Smazat` (bezpečné dvoukrokové smazání pro uložené YAML projekty).
- **Přetažení souboru z počítače (Drag & Drop)** nebo tlačítko **Nahrát .hex / .bin / .yaml**: automaticky převede a uloží databázi jako nový projekt v pracovní složce.
- **Vyhledávání a filtry**: podle textu i typu (`Vše`, `Projekty YAML`, `HEX / BIN`, `gBUSE0`, `gBUSE1`).
- **Název a popis projektu**: upravují se při vytvoření (`Nový…`), přes `Uložit jako…` nebo kdykoliv v záložce **Konfigurace** (`meta.name`, `meta.description`; na binární výstup `.hex`/`.bin` nemají žádný vliv).

### Záložka Texty
- **Seznam sekcí** se přizpůsobuje formátu:
  - *gBUSE1*: Cíle (`z`), Linky (`l`), Zastávky (`x`), Mimořádné (`xA`), Cíle 4místné (`zC`), Doplňkové texty (`DOP`), Formátovací řetězce (`STR`).
  - *gBUSE0*: Cíle (`z`), Linky (`l`), Nácestné zastávky (`zD`), Cíle 4místné (`zC`), Formátovací řetězce (`STR`).
- **Editor gBUSE1**: editace čísla a textu v běžném režimu (font, mezera, zarovnání nebo `ESC s`, efekt `t1`–`t3`, blikání `b`, interval `z`, inverze), tlačítko **Automat** pro výběr největšího padnoucího fontu, přepnutí na značky `{…}` i obousměrný převod **MNEMO (gBUSE1)**.
- **Editor gBUSE0**:
  - Režim **1 řádek**, **2 řádky** (víc variant na 2. řádku oddělených svislítkem `|` automaticky vytvoří střídanou smyčku `LF … VT`) nebo čistý **MNEMO kód**.
  - **Automatický formát / Naformátovat nyní** vybírá `(font, mezera, svislý posun)` z tabulek `AFC` (1řádkové) nebo `AFD` (2řádkové).
  - Tlačítko **Prohodit řádky**, volby **Celý panel (`ESC b`)**, **Skrýt zastávky (`ESC w`)**, **Interval střídání (`ESC z`)** a 12znakový **Ekvivalent BS100/BS230**.
- Tlačítko **Kontrola** spustí `Project.check()` (chybějící znaky v kódové tabulce nebo fontu, přetečení textu přes okno, duplicity klíčů a u gBUSE0 také varování limitů BS110).

### Záložka Cykly & DOP *(gBUSE1)*
- Záložky cyklů `0–9`, přepínač zobrazení hodnot **Názvy / DEC / HEX**.
- Tabulka kroků s odděleným nastavením pro **Levé okno (L)** a **Pravé okno (P)**: šířka v sloupcích (`0` u P = zbytek panelu), zdroj obsahu `0–14` (linka, cíl, zastávka, mimořádná, čas, pásmo…), index prefixu `DOP`, efekt vstupu/výstupu `0–4`, textové `ID` kroku a doba trvání v sekundách.
- Přímé přehrání vybraného cyklu na horním LED náhledu i import/export samostatného `.cyk`.

### Záložka Konfigurace
- **Společné**: typ panelu, verze FW, IBIS adresa, počet sloupců (u gBUSE0 i řádků), text odpovědi `aV`, zaškrtávací pole povolených IBIS povelů a 128B hex tabulka `KOD`.
- **Specifika gBUSE1**: výchozí font/mezera/interval, zpoždění cyklu, `panel_kind` (`0` klasický, `1` BS120, `2` s cykly + DOP), počet číslic tarifního pásma (`1–3`).
- **Specifika gBUSE0**: číslo panelu, jas (`0–255`), oddělovač titulku `FNT` (`:`, mezera, tečka), příznaky `P1–P5` (bajt `0x0B`), předvolby rozložení (`Přední`, `Boční`, `Boční + nácestné`, `Zadní`), geometrie polí `line`/`dest`/`stop` v souřadnicích `1..N` odspodu a editovatelné tabulky `AFC`/`AFD`.

### Záložka Fonty a ikony
- Podporuje znaky proměnné výšky **1 až 28 bodů** (`± sloupec`, `± řádek`, posuny šipkami, vymazání, inverze) a vložení sady ikon do fontu 14.
- **Správce fontů**: vytvoření prázdného fontu, kopírování vybraného znaku, celého fontu nebo všech fontů ze současného projektu, z vestavěných originálních fontů `gBUSE0`/`gBUSE1` nebo z jiného souboru `.hex`/`.bin`/`.yaml`; mazání fontu či znaku ze všech fontů; import/export binárního `.fnt`.
- **Kódová tabulka**: editace mapování kódů `0x21–0xAF` a `0xF0–0xFF` na znaky Windows-1250.

### Záložka Import / Export
- **CSV / Excel**: hromadný import textů podle sloupců `cislo`, `text`, `sekce`, `font`, `mezera`, `zarovnani`, `bezici`, `inverze` (+ stažení vzorového CSV).
- **Nativní soubory sekcí gBUSE**: import i export samostatných souborů `.lin`, `.cil`, `.ci4`, `.dru`, `.zst`, `.mim`, `.dop`, `.cyk`, `.kod`, `.aL`/`.LA`, `.afm`/`.afc`/`.afd`, tabulátorových `.txt` (včetně `$` šablon jako `[$1]`, `($2)` a DOS `@` souborů v kódování Kamenických) a ekvivalentů `.bbd`/`.bbf` s řazením podle čísla nebo abecedy.

---

## 4. Jazyk MNEMO a řízení zobrazení

### gBUSE0 MNEMO (`busepanel/gbuse0/mnemo.py`)

| Token | Binární kód | Význam |
|---|---|---|
| `'text'`, `#N` | znaky / bajt `N` | textový řetězec (apostrof přes `#39`) nebo surový desítkový kód `32..175`, `240..255` |
| `f0..f15` | `E0..EF` | výběr fontu 0–15 |
| `m0..m15` | `B0..BF` | mezera mezi znaky 0–15 bodů |
| `y0..y31` | `C0..DF` | svislý posun dolů od horního okraje okna |
| `l1..224`, `p1..224` | `1B 6C/70 (n+0Fh)` | levý / pravý sloupec okna (1-based) |
| `h1..28`, `d1..28` | `1B 68/64 (n+0Fh)` | horní / dolní řádek okna počítaný zdola (1-based) |
| `s1..224` | `1B 73 (n+0Fh)` | počáteční sloupec textu (jinak centrováno) |
| `z0..239` | `1B 7A (n+0Fh)` | doba zobrazení snímku v desetinách sekundy |
| `c`, `i`, `o`, `x` | `1B 63/69/6F/78` | reset okna na pole / inverze / vymazání / překrytí bez smazání |
| `n`, `r` | `1B 6E/72` | rotace obsahu okna o řádek nahoru / o sloupec doleva |
| `b`, `w` | `1B 62/77` | blokovat ostatní telegramy (celý panel) / potlačit nácestné zastávky |
| `FF`, `LF`, `VT`, `CR` | `0C`, `0A`, `0B`, `0D` | formát / konec řádku či snímku / smyčka od 1. `LF` / konec |

### gBUSE1 značky a MNEMO (`busepanel/core/text.py`)

| Značka `{…}` | MNEMO | Binárně | Význam |
|---|---|---|---|
| `{f3}` | `f3` | `E3` | font 3 |
| `{m1}` | `m1` | `B1` | mezera 1 sloupec |
| `{t0}`–`{t3}`, `{run}` | `t0`–`t3` | `1B 74 10..13` | `0` statický, `1` běžící, `2` posuv zprava, `3` nahrnutí zleva |
| `{blink}` | `b` | `1B 62` | blikání textu |
| `{inv}` | `i` | `1B 69` | inverze |
| `{x:24}` | `s25` | `1B 73 (10h+x)` | počáteční sloupec (0-based v `{x:…}`, 1-based v MNEMO `s`) |
| `{z:15}` | `z15` | `1B 7A (0Fh+n)` | doba stránky v 0,1 s |
| `{l:1}`, `{p:135}` | `l1`, `p135` | `1B 6C/70 (0Fh+n)` | levý/pravý okraj okna |
| `{ff}`, `{lf}` | `FF`, `LF` | `0C`, `0A` | formátování / konec stránky |

---

## 5. Použití z příkazové řádky (CLI)

Příkazy pro databázi gBUSE automaticky podporují `.yaml`, `.hex` i `.bin`:

```bash
# Založení nového jedno- nebo vícepanelového projektu
busepanel new projekt.yaml --format gbuse1 --version "LINKY"
busepanel new vnejsi.yaml  --format gbuse0 --preset side_via --version "BS210"
busepanel new-bundle projekty/demo --template demo_bus
busepanel new-bundle projekty/prazdny --template empty_bus_3

# Převod HEX -> YAML a zpět
busepanel import-hex data.hex projekt.yaml
busepanel export-hex projekt.yaml vystup.hex

# Kontrola projektu a náhled v terminálu nebo PNG (pro gBUSE0 i gBUSE1)
busepanel check projekt.yaml
busepanel render vnejsi.yaml 001 --line 001 --stop 0001 --png nahled.png
busepanel render projekt.yaml 001 --line 010

# Hromadný import z CSV/XLSX
busepanel import projekt.yaml texty.csv

# Nahrání do panelu po IBIS a stažení kompletní zálohy (.bin + .hex + .yaml)
busepanel upload projekt.yaml -a 10
busepanel backup -o zaloha -a 10
```

---

## 6. HTTP API modulu gBUSE

Webové rozhraní komunikuje se serverem ([`busepanel/server/app.py`](../busepanel/server/app.py)) přes následující endpointy:

| Endpoint | Metoda | Účel |
|---|---|---|
| `/api/files`, `/api/project/open`, `/api/project/save`, `/api/project/update`, `/api/project/close` | `GET`/`POST` | Výpis s metadaty projektů, otevření (`.hex`/`.bin`/`.yaml`), uložení, synchronizace a zavření |
| `/api/project/new`, `/api/project/upload-open`, `/api/project/save-as`, `/api/project/duplicate`, `/api/project/delete` | `POST` | Správa jedno- i vícepanelových projektů: založení, nahrání z počítače, uložení jako, duplikování a smazání |
| `/api/bundle/new`, `/api/bundle/select-panel`, `/api/bundle/add-panel`, `/api/bundle/remove-panel`, `/api/bundle/update-meta` | `POST` | Vytvoření zabudované sady (`demo_bus`/`empty_*`), přepínání a přidání/odebrání panelů, metadata |
| `/api/bundle/propagate`, `/api/bundle/preview-all`, `/api/bundle/export-zip`, `/api/bundle/apply-sim` | `GET`/`POST` | Rozkopírování záznamu mezi panely, společný náhled všech panelů, ZIP export, načtení do simulace |
| `/api/live/templates`, `/api/live/templates/save` | `GET`/`POST` | Sdílené a projektové předvolby pro Živě (`zive/*.yaml` a `zive.yaml` v projektu) |
| `/api/project/check`, `/api/project/hex`, `/api/project/bin` | `GET` | Validace projektu a stažení Intel HEX nebo surového BIN obrazu |
| `/api/preview` | `POST` | Složení živého náhledu (gBUSE0 vícepólový obraz, gBUSE1 efekty/stránky i kroky cyklu) |
| `/api/gbuse0/compose`, `/api/gbuse/autoformat`, `/api/mnemo/convert` | `POST` | Skladba záznamu gBUSE0, automatické formátování a převod do/z MNEMO |
| `/api/fonts`, `/api/fonts/glyph`, `/api/fonts/glyph/delete`, `/api/fonts/codetable` | `GET`/`POST` | Čtení fontů (výška 1–28), editace/mazání znaků a kódové tabulky (`0x21–0xAF`, `0xF0–0xFF`) |
| `/api/fonts/manage`, `/api/fonts/export-fnt`, `/api/fonts/import-fnt`, `/api/fonts/add-icons` | `GET`/`POST` | Správce fontů (kopie/mazání), export/import `.fnt` a vložení ikon |
| `/api/import`, `/api/gbuse/section/import`, `/api/gbuse/section/export` | `POST` | Import CSV/XLSX a nativních sekcí gBUSE (`.lin`, `.cil`, `.dru`, `.cyk`, `.dop`, `.afm`, `.bbd`, `.txt` aj.) |
