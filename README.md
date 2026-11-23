# BUSE Studio

Otevřená náhrada programů **gBUSE** a **BSLoader** pro informační panely BUSE do autobusů, tramvají a trolejbusů. Umí i živé ovládání panelu a simulaci jízdy (například pro OMSI).
Ověřeno na vnitřním panelu **BS 120**. Běží na Windows, Linuxu i macOS.

> Projekt nijak nesouvisí s firmou BUSE ani s jejími nástupci. Protokol a formát dat byly zjištěny
> analýzou kvůli interoperabilitě. Popis je v [docs/PROTOKOL.md](docs/PROTOKOL.md).
> Repozitář neobsahuje žádná data dopravců ani původní software. Obsahuje originální font gBUSE (fonty 0–3 a 15), výchozí pro univerzální databázi, a originální fonty gBUSE0 (0–12) pro vnější panely; volitelně vestavěný font (`--font builtin`).

## Co umí

| Modul | Náhrada za | Popis |
|---|---|---|
| **gBUSE** | gBUSE1 | Editor dat panelu: linky, cíle, zastávky, konfigurace, fonty a ikony, import z CSV/Excelu. Data jsou v čitelném YAML a převádějí se z/do HEX beze ztráty. |
| **Loader** | BSLoader | Vyhledání panelu, nahrání dat s kontrolou, záloha, konzole IBIS povelů. |
| **Živě** | – | Panel jako displej: volný text, hodiny, odjezdová tabule. |
| **Simulace** | – | Linka se směry a kurzy, vozidlo s libovolným počtem periferií (vnitřní BS120, vnější DOT-LED v1–v4, LCD), překlápění stránek (příští zastávka, směr, zóna, čas), mimořádnosti, API pro externí řízení a WebSocket pro ESP brány. |

## Instalace

Potřebuješ Python 3.10+ a USB převodník na sběrnici IBIS (ověřeno s Prolific PL2303).

```bash
git clone <url> buse-studio
cd buse-studio
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[excel,test]"
```

**Linux:** uživatel musí mít přístup k sériovému portu, převodník bývá `/dev/ttyUSB0`:

```bash
sudo usermod -aG dialout $USER     # pak se odhlásit a znovu přihlásit
```

## Zprovoznění na čistém počítači (panel přes USB převodník)

```bash
mkdir ~/panel && cd ~/panel
busepanel scan                      # najde panel a jeho IBIS adresu (např. "adresa 10: aV…")
busepanel quickstart -a 10          # připraví pracovní složku (viz níže)
busepanel upload databaze/univerzalni/projekt.yaml -a 10
#   -> panel VYPNOUT A ZAPNOUT (čísla linek se z nových dat načtou až po restartu)
busepanel serve                     # otevře http://127.0.0.1:8090
```

`quickstart` vytvoří:
- `databaze/univerzalni/projekt.yaml`: univerzální databázi s originálním fontem gBUSE (tenký 0, normální 3; `--font builtin` = vestavěný font), ikonami (přímo v textových fontech na kódech 0xAA–0xAE, 0xF0, 0xF5–0xFE) a čísly linek. Ostatní texty pak aplikace posílá dynamicky volným textem.
- `trasy/`: ukázkovou trasu regionální linky 607 Pardubice → Chlumec nad Cidlinou s tarifními zónami IDS IREDO. Zdroj: veřejná data CIS JŘ a IREDO.
- `vozidla/autobus.yaml`: vozidlo s náhledem a se skutečným panelem na nalezeném převodníku.

Vozidla lze třídit do libovolných podsložek `vozidla/` (např. zvlášť testovací jen v počítači a zvlášť
s fyzickým panelem); v seznamu vozidel se podsložky zobrazí jako skupiny.

Pak v prohlížeči: **Jízda** → vybrat linku a vozidlo → **▶ Start**. Mezerníkem posouváš jízdu, šipkou ← jdeš zpět, A je příjezd, D odjezd.

Port lze zadat přes `-p`, například `-p /dev/ttyUSB0` nebo `-p COM5`, případně proměnnou `BUSE_PORT`. Jinak se převodník najde automaticky.

## Příkazová řádka

| Příkaz | Co dělá |
|---|---|
| `busepanel scan` | najde panely (povel `aV`) |
| `busepanel send l010 z001` | pošle IBIS povely |
| `busepanel query aV:` | dotaz s odpovědí |
| `busepanel import-hex data.hex projekt.yaml` | HEX z gBUSE → čitelný projekt |
| `busepanel export-hex projekt.yaml data.hex` | projekt → HEX |
| `busepanel check projekt.yaml` | kontrola (chybějící znaky, přetečení) |
| `busepanel render projekt.yaml 001 --line 010` | náhled v terminálu nebo PNG (`--png`) |
| `busepanel import projekt.yaml cile.csv` | hromadný import |
| `busepanel upload projekt.yaml` | nahrání do panelu s kontrolou |
| `busepanel backup -o zaloha` | stažení dat z panelu (`.bin`, `.hex`, `.yaml`) |
| `busepanel live clock --project p.yaml` | hodiny volným textem |
| `busepanel live departures --project p.yaml --schedule odjezdy.csv` | odjezdová tabule |
| `busepanel quickstart` | příprava pracovní složky |
| `busepanel serve` | grafické rozhraní |

## Ověřené chování BS 120 (podrobně v docs/PROTOKOL.md)

**Sběrnice a nahrávání:**
- IBIS: 1200 Bd, 7E2, **DTR musí být 0**.
- Servisní režim (`ESC S <adr>`) běží na 9600 Bd, s binárními rámci; mazání `07`, zápis `05`, čtení `06`.

**Volný text:**
- `v<text>` + `x9996` = jen text, `x9997` = linka + text, `x9999` = zpět linka + cíl.
- Po přepnutí režimu panel ukáže předchozí text, proto se nový text posílá až po přepínači.

**Pole linky:**
- `l<NNN>` bere jen poslední dvě číslice, takže míst je 100. Linky nad 99 mapuje univerzální databáze na volná místa.
- Po nahrání nových dat je nutné panel vypnout a zapnout.

**Rozložení displeje** (135 sloupců):

| Oblast | Sloupce |
|---|---|
| pole linky | 0–23 |
| cíl na střed | 24–134 |
| text s `ESC s` | jen do sloupce 111 |

## Další typy panelů

Profily jsou v [`busepanel/panels/profiles.yaml`](busepanel/panels/profiles.yaml). BS 220 (dvouřádkový) je zatím jen
odhad. gBUSE1 1.12 s cykly zatím podporovaný není.

**Vnější panely (BS 210, gBUSE0)** se v simulaci emulují: čtení databáze gBUSE0 (`.hex`) a interpret záznamů
([`busepanel/gbuse0/`](busepanel/gbuse0)). Bez databáze se použije varianta podle modelu: v1 = BS 210.0C čelní
140×19, v2 = BS 210.0B boční 112×19, v4 = BS 210.0A zadní 28×19, s originálními fonty gBUSE0. Jako `type` jde zadat
i přesný model (`BS 210.0B`, starší `BS 210.B`, zářivkový `BS 110.19-112-1`) – varianta se pak vybere sama. Zápis databáze gBUSE0 zatím není.
Co je ověřené a co odhad, popisuje [docs/PROTOKOL.md](docs/PROTOKOL.md), kap. 4.6 a 10.

## Simulace: vozidlo, linka a skladba textů

Vozidlo (`vozidla/*.yaml`) má libovolný počet panelů. Každý panel je buď fyzický (COM port + adresa IBIS),
na síti (ESP), nebo virtuální náhled. Panely na stejném COM portu nebo ESP zařízení sdílejí jednu linku IBIS.
Typy panelů jsou v [`busepanel/peripherals/`](busepanel/peripherals/__init__.py).

```yaml
panels:
  - {name: Vnitřní, type: BS120, role: interior, output: serial, port: "", address: 10}   # "" = nalezený převodník
  - {name: Čelní, type: BS210, variant: v1, role: front, output: serial, port: ""}
  - {name: Boční, type: BS210, variant: v2, stop_field: bottom, role: side, output: virtual}
  - {name: Zadní, type: BS210, variant: v4, role: rear, output: virtual}
  - {name: LCD, type: LCD, role: interior, http_port: 8091}
```

`stop_field: top | bottom` přidá vnějšímu panelu řádek nácestných zastávek nad nebo pod cíl (`stop_rows` = výška).

Linka (`trasy/*.yaml`) může mít **směry** a **kurzy**, podobně jako je zná BS100. Kurz je řada spojů a spoj může
jet jen část trasy. Stará „plochá“ trasa se načte jako jeden směr. „Přes“ jsou zastávky označené `via: true`
(starý textový údaj `via: "A - B"` se při načtení převede na označení zastávek).

```yaml
line: "3"
directions:
  A: {destination: LÁZNĚ BOHDANEČ, stops: [{name: Hlavní nádraží}, {name: "Semtín, zastávka", via: true}, ...]}
  B: {destination: HLAVNÍ NÁDRAŽÍ, stops: [...]}
courses:
  "1":
    - {direction: A, departure: "05:12"}
    - {direction: B, departure: "06:05"}
    - {direction: A, departure: "07:00", to: "Semtín, zastávka"}   # zkrácený spoj – cíl = poslední zastávka
```

Na stránce **Linky** se edituje celý soubor: záložky směrů (cíl, kód cíle, zastávky; „Obrácený směr“ založí
protisměr), kurzy se seznamem spojů (směr, odjezd, Od / Do pro zkrácený spoj, vlastní cíl) a mimořádnosti
společné pro linku. Simulovaný spoj se vybírá v Jízdě nebo tlačítkem ▶ u spoje; jeho zastávky jsou v editoru
směru podbarvené. Tlačítko **Další spoj** (událost `next_trip`) přejde na další spoj kurzu. Stará plochá trasa
se uloží zase plochá, dokud nepřibude druhý směr nebo kurz. API: `POST /api/sim/trip` s
`{"course": "1", "index": 0}` nebo `{"direction": "A"}`; `POST /api/sim/line/update` / `line/save` s celou linkou.

**Běžící text:** stránka, která se na vnitřní panel nevejde (nebo má „Běž.“), se zobrazí přesně tak dlouho,
aby text jednou projel: (šířka textu + šířka pole) sloupců × rychlost posuvu. Pole je část vedle čísla linky,
nebo celý panel. Volba „Běžící text: aspoň doba položky“ (`run_time: min`) vrátí dřívější chování.
Rychlost posuvu je v profilu panelu (`scroll_ms`, BS120: 20 ms/sloupec, odhad) a u každého panelu ve vozidle
ji jde zkalibrovat polem „Posuv [ms/sloupec]“.

**Skladba textů** (formáty) je v YAML: vestavěné v [`busepanel/sim/formats/`](busepanel/sim/formats), vlastní ve
složce `formaty/` pracovní složky (stejná struktura, podsložky = skupiny). Upravují se na stránce **Formáty textů**
s živým náhledem na zvoleném panelu (vnitřní BS120, čelní, boční, zadní) a v zvolené situaci (na trase, v zastávce,
konečná, mimořádnost) nad načtenou linkou. Vestavěné formáty nejdou přepsat, ukládají se jako kopie.
Vozidlo si formát jen vybírá (`settings.interior.format`) a do svého souboru ukládá jen to, čím se od formátu liší;
úprava formátu se tak projeví ve všech vozidlech, která ho používají. Starší vozidla s úplnou kopií nastavení
fungují dál – při uložení se zjednoduší a jejich vlastní skladbu jde tlačítkem uložit jako nový formát. Sekvence `exterior` (případně `exterior_side`,
`exterior_rear`) určuje cíl na vnějších panelech, sekvence `via` řádek nácestných zastávek. Stránka s
`{via_stop}` se zopakuje pro každou zbývající nácestnou zastávku a panel je sám překlápí. Bez fontu si vnější
panel text naformátuje sám.

## Doplňky

Doplňky nejsou součástí hlavní funkce BUSE Studia (náhrada gBUSE a BSLoaderu, panely BUSE). Jsou to volitelné
rozšíření, každé ve vlastním balíčku v `busepanel/extras/` s vlastním kódem i webovými soubory.

### LCD panel (`busepanel/extras/lcd`)

Výstup simulace „LCD“ není panel BUSE, ale webová stránka ve stylu informačních LCD panelů: číslo linky, pásmo,
příští zastávky s minutami, čas a konečná; zastávka, ve které vůz stojí, je zvýrazněná, zastávka na znamení
má zvonek.
Hodí se třeba pro OMSI nebo tablet ve vozidle. Každý LCD panel vozidla má vlastní HTTP port:

```yaml
panels:
- {name: LCD, role: interior, output: lcd, http_port: 8091}           # http://127.0.0.1:8091/
- {name: LCD tablet, role: interior, output: lcd, http_port: 8092, host: 0.0.0.0}   # i z jiných zařízení v síti
```

Stejná stránka je i na hlavním portu na `/lcd/`. Minuty se zobrazí, když mají zastávky v trase `minutes`
(minuty od začátku spoje).

### Ikony zastávek

Ikony zastávek (`metro-A` … `metro-D` → stylizované `[B]`, `vlak`/`S9` → vlak, `letadlo`, `privoz`, `vyluka`,
`cd`, `sipka` podle PID; `bus`, `tramvaj`, `trolejbus`, `vozik`, `prestup`, `pozor` vlastní) zobrazují jen LED panely.
LCD panel piktogramy nezobrazuje, jen zvonek u zastávky na znamení (`request: true`).

## ESP brány

Panel s výstupem „ESP“ dostává hotové IBIS telegramy přes WebSocket. Protokol je v [docs/esp-protokol.md](docs/esp-protokol.md).

## Vývoj

```bash
pytest                                        # testy s vestavěným fontem
BUSE_SAMPLE_DIR=/cesta/k/vlastnim/hex pytest  # + round-trip vlastních souborů z gBUSE
```

## Licence

MIT, viz [LICENSE](LICENSE).
