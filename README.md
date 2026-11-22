# BUSE Studio

Otevřená náhrada programů **gBUSE** a **BSLoader** pro informační panely BUSE do autobusů, tramvají a trolejbusů. Umí i živé ovládání panelu a simulaci jízdy (například pro OMSI).
Ověřeno na vnitřním panelu **BS 120**. Běží na Windows, Linuxu i macOS.

> Projekt nijak nesouvisí s firmou BUSE ani s jejími nástupci. Protokol a formát dat byly zjištěny
> analýzou kvůli interoperabilitě. Popis je v [docs/PROTOKOL.md](docs/PROTOKOL.md).
> Repozitář neobsahuje žádná data dopravců ani původní software. Obsahuje originální font gBUSE (fonty 0–3 a 15), výchozí pro univerzální databázi; volitelně vestavěný font (`--font builtin`).

## Co umí

| Modul | Náhrada za | Popis |
|---|---|---|
| **gBUSE** | gBUSE1 | Editor dat panelu: linky, cíle, zastávky, konfigurace, fonty a ikony, import z CSV/Excelu. Data jsou v čitelném YAML a převádějí se z/do HEX beze ztráty. |
| **Loader** | BSLoader | Vyhledání panelu, nahrání dat s kontrolou, záloha, konzole IBIS povelů. |
| **Živě** | – | Panel jako displej: volný text, hodiny, odjezdová tabule. |
| **Simulace** | – | Trasa linky, vozidlo s více panely, překlápění stránek (příští zastávka, směr, zóna, čas), mimořádnosti, API pro externí řízení a WebSocket pro ESP brány. |

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
- `databaze/univerzalni/projekt.yaml`: univerzální databázi s originálním fontem gBUSE (tenký 0, normální 3; `--font builtin` = vestavěný font), ikonami (font 14) a čísly linek. Ostatní texty pak aplikace posílá dynamicky volným textem.
- `trasy/`: ukázkovou trasu regionální linky 607 Pardubice → Chlumec nad Cidlinou s tarifními zónami IDS IREDO. Zdroj: veřejná data CIS JŘ a IREDO.
- `vozidla/autobus.yaml`: vozidlo s náhledem a se skutečným panelem na nalezeném převodníku.

Pak v prohlížeči: **Simulace** → načíst trasu → **▶ Start**. Mezerníkem posouváš jízdu, šipkou ← jdeš zpět, A je příjezd, D odjezd.

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

Profily jsou v [`busepanel/panels/profiles.yaml`](busepanel/panels/profiles.yaml). BS 220 (dvouřádkový) a BS 210 (vnější) jsou zatím jen odhad. Formát gBUSE0 (vnější panely) ani gBUSE1 1.12 s cykly zatím podporovaný není.

## ESP brány

Panel s výstupem „ESP“ dostává hotové IBIS telegramy přes WebSocket. Protokol je v [docs/esp-protokol.md](docs/esp-protokol.md).

## Vývoj

```bash
pytest                                        # testy s vestavěným fontem
BUSE_SAMPLE_DIR=/cesta/k/vlastnim/hex pytest  # + round-trip vlastních souborů z gBUSE
```

## Licence

MIT, viz [LICENSE](LICENSE).
