# BUSE panely – protokol a formát dat
> **Poznámka k repozitáři:** tento dokument shrnuje analýzu protokolu a datového formátu panelů BUSE
> provedenou kvůli interoperabilitě. Analytické skripty a výpisy pamětí zmiňované v textu
> (`nastroje/…`, `vypis_panelu/…`) ani původní programy a data dopravců nejsou součástí repozitáře.
> Jejich funkce jsou v balíčku `busepanel`:
>
> | Funkce | Kde je v `busepanel` |
> |---|---|
> | IBIS | `core/ibis.py` |
> | servisní režim | `core/link.py` |
> | nahrávání a záloha | `loader/device.py`, příkazy `busepanel upload` / `backup` |
> | formát gBUSE1 | `core/image.py`, `core/project.py` |
> | fonty | `core/fonts.py` |


Stav analýzy k 22. 11. 2026. Zdroje: reverse engineering `BSLoader_4_02.exe` (Delphi), zdrojáky MyFaPixel a ověření na živém panelu **BS120 na COM5**.

Značení spolehlivosti:
- **[OVĚŘENO]** – vyzkoušeno na panelu
- **[KÓD]** – vyčteno z disassembly BSLoaderu, na panelu zatím nezkoušeno
- **[ODHAD]** – odvozeno z dat, nepotvrzeno

---

## 1. Fyzická vrstva

| Parametr | Hodnota |
|---|---|
| Rozhraní | sběrnice IBIS přes převodník (zde Prolific USB-Serial, COM5) |
| DTR | **musí být 0** – při DTR=1 panel neodpovídá **[OVĚŘENO]** |
| RTS | na výsledek nemá vliv (BSLoader ho nastaví a pak shodí do 0) |
| Normální režim | **1200 Bd, 7 datových bitů, sudá parita, 2 stop bity** (IBIS) **[OVĚŘENO]** |
| Servisní režim | **9600 Bd 8N1** pro BS120 **[OVĚŘENO]**. Jiné typy mohou používat 19200 (BSLoader bere hodnotu z tabulky zařízení.) |
| BS100 (palubní počítač) | 115200 Bd 8N1, binární rámce **[KÓD]** |

BSLoader otevírá port s DCB `Flags = 1` (jen fBinary), takže DTR i RTS zůstávají neaktivní. pyserial je ve výchozím stavu zapíná, proto je nutné nastavit `s.dtr = False` **před** `open()`.

## 2. Vrstva IBIS (normální provoz)

Telegram: `TEXT` + `CR (0x0D)` + `P`

- `P = 0x7F XOR (XOR všech bajtů TEXT a CR)`, tj. standardní kontrolní znak VDV 300 **[OVĚŘENO]**.
- Znaky ≥ 0x80 se posílají jako `0x0E` + `(znak & 0x7F)` **[KÓD]**. Odesílací rutina BSLoaderu (0x473CF8) má chybu a místo 0x0E zapíše znak dvakrát. Přijímací strana (0x473C24) escape správně zpracuje.
- **IBIS adresa** 0–15 se kóduje znaky `0123456789:;<=>?` (A–F → `:`–`?`). Pokud je zapnutá volba „alternativní adresy“, posouvá se adresa o 0x40 na `@`–`O`.

### Povely, které BSLoader zná
Seznamy povelů podporovaných jednotlivými typy zařízení jsou v EXE (zobrazují se v okně Status):
- `a, aA, aL, aSC, aSL, aSX, aSZ, aV, k, l, lA, z, zA, zC, zD, zI`
- `e, hE, hP, u, v, x, xA, xB, xC, zM, zN, aI, aP, aS, aT, aX, aY`

Význam podle standardu IBIS a toho, co dělá BSLoader:

| Povel | Význam |
|---|---|
| `aV<adr>` | dotaz na verzi dat **[OVĚŘENO]** |
| `l<nnn>` | číslo linky |
| `z<nnn>` | číslo cíle |
| `zI…`, `x…`, `e…` | další texty a zóny (viz okno Status: „Počet zón v povelu e“) |
| `ESC S <adr>` | přepnutí zařízení do servisního režimu (kap. 3) **[OVĚŘENO]** |

Typ zařízení BSLoader určuje z odpovědi na `aV` podle podřetězců `HW3100`→BS 214/224, `HW3200`→BS 228, `HW3300`→BSE 270, `HW3400`→BS 370, `HWP210`→BS 210F, `HW:B24`→BS 410, `HW:B19`→BS 310 a také `EEPROM`/`FLASH`.

**Náš panel:** `aV:` → `aVNOVa` (+CR+P). To je pouze verze dat „NOVá“ (7bitový přenos z `á` udělá `a`). Starý firmware tedy HW řetězec nevrací.

## 3. Servisní režim (čtení, mazání, zápis FLASH)

### 3.1 Vstup **[OVĚŘENO]**
1. 1200 7E2: prázdný telegram `CR P` (`0D 72`)
2. 1200 7E2: telegram `ESC 'S' <adr>`, např. `1B 53 3A 0D P`
3. přepnout port na 9600 8N1, počkat cca 200 ms

### 3.2 Binární rámec **[OVĚŘENO]**
```
LEN  DATA[LEN]  CS          CS = -(LEN + ΣDATA) mod 256
```
Zařízení odpoví jedním bajtem:

| Bajt | Význam |
|---|---|
| `'O'` (0x4F) | OK |
| `'E'` (0x45) | chyba |
| `'R'` (0x52) | opakovat (špatný CS) |

Pokud příkaz vrací data, následuje rámec stejného formátu `LEN DATA CS`. BSLoader opakuje odeslání 2–4×. Při chybném CS přijatého rámce pošle `'R'`, nejvýše 5×.

### 3.3 Příkazy (první bajt DATA)
| Kód | Parametry | Význam | Stav |
|---|---|---|---|
| `0x08` | `addrLo addrHi n` | čtení n bajtů **programové paměti** (EPROM 8051) | **[OVĚŘENO]** |
| `0x06` | `addrLo addrHi bank n` | čtení n bajtů **FLASH s daty** (bank 0/2) | **[OVĚŘENO]** |
| `0x0F` | – | ukončení servisního režimu, návrat na IBIS | **[OVĚŘENO]** (odpověď `O`) |
| `0x05` | `a0 a1 a2 data…` | zápis do FLASH, 24bit adresa (LSB první), data = jeden řádek HEX | **[OVĚŘENO]** |
| `0x07` | – (BS120) / `00` | **mazání databáze** u zařízení typu 2/4/7 (BS 120 = typ 4, bez parametru) | **[OVĚŘENO]** |
| `0x24` | `a0 a1 a2 data…` | zápis u zařízení typu 3/5 (BS 410, BS 220) | [KÓD] |
| `0x03` | `sektor` | mazání sektoru 0–7 („celá Flash“) | [KÓD] |
| `0x03` | `adrL adrH` + 32 B | „příprava panelu“: BSLoader zapíše 8 KB bajtů `01` od adresy 0; účel neznámý, **vynecháno** | [KÓD] |
| `0x22` | `0x00` | mazání programu | [KÓD] |
| `0x23` | – | mazání databáze (typy 3/5) | [KÓD] |
| `0x01` | `0x21 0 0 0 0` | před mazáním z menu Mazání | [KÓD] |
| `0x12` | `0x00` | před mazáním z menu Mazání | [KÓD] |
| `0x15` | `0x55` | po nahrání (typ 210) | [KÓD] |
| `0x05` | `05 00 00 55 55 55 55` | po nahrání **programu** zapíše značku `55555555` na adresu 5 | [KÓD] |

Kód typu zařízení přiřazuje BSLoader podle názvu: 100→0, 230→1, 110→2, 210→3, 210F→4… a **120→4**, 220→5. Seznam je v `BSLoader` na adrese 0x48BD80.

### 3.4 Nahrání HEX do BS120 **[OVĚŘENO]**
Nástroj `busepanel upload` (dříve analytický skript `nahraj.py`) dělá totéž co BSLoader pro BS120 (typ 4), jen bez „přípravy panelu“:
1. vstup do servisního režimu (3.1),
2. `07` mazání databáze. `O` přijde asi po 0,7 s a čtení pak vrací `FF`.
3. Každý datový záznam Intel HEX se pošle jako `05 adrL adrM adrH <data>` a čeká se na `O`. Rozšířené adresy se berou ze záznamů typu 02/04.
4. kontrolní přečtení (`06`) celého obrazu,
5. `0F` konec. Panel hned pracuje s novými daty, verze dat z `aV` se změní.

**Úskalí, na která jsme narazili:**
- **Panel potvrdí `O` dřív, než dokončí programování FLASH**, a v té době ztrácí přijímané bajty. Bez pauzy se asi u každého desátého rámce ztratilo potvrzení. Panel pak zůstane uprostřed rámce a na všechny další rámce stejné délky odpovídá `R`.
  - Řešení: **pauza 80 ms po každém `O`**. S ní proběhl zápis 169 řádků za 21 s bez jediné chyby.
  - Při chybě se spojení obnoví tak, že se posílají jednotlivé bajty `00`, dokud panel neodpoví (`00 00` je platný prázdný rámec), a pak se ověří čtením.
- Pokud zápis spadne v polovině, panel zůstane v servisním režimu na 9600 Bd. Stačí znovu otevřít port, poslat `0F` a nahrát data znovu.
- Bez platných dat (smazaná FLASH) panel nejspíš použije výchozí hlavičku uloženou v EPROM za identifikací firmwaru (`AB 00 86 02 88 81 …`).

### 3.5 Chování panelu BS120 při zobrazování **[OVĚŘENO]**
- Text se překreslí jen při **změně čísla** linky nebo cíle. Stejné `z001` po nahrání nových dat obraz nezměnilo, `z002` ano.
- Panel má **135 sloupců** (0–134), jak udává hlavička. Pole linky zabírá sloupce ~0–23.
- **Text bez `ESC s`** (vycentrovaný) využije pole cíle až k pravému okraji, tedy sloupce ~24–134 („LÁZNĚ BOHDANEČ“).
- **Text s `ESC s n`** se kreslí od **absolutního sloupce panelu** n−0x10, ale jen do oblasti **0–111**. Za sloupcem 111 se ořízne a sloupce 112–134 zůstanou prázdné. Co padne do pole linky (<24), je zakryté.
- Ověřeno měřením se třemi pravítky (znaky se známými sloupci):

  | `ESC s` | Výsledek |
  |---|---|
  | 0 | viditelné od kusu D (sloupec ~24) po useknuté Q (111), pak mezera |
  | 24 | od celého A po useknuté N (111), pak mezera |
  | 69 („VPRAVO“, 43 sloupců) | 69–111, pak mezera asi 3 písmena |

- Povely `u` (čas) ani `e` (pásmo) pruh 112–134 nezaplnily.
- **Pravidla pro sestavení textu na tomto panelu:**

  | Zarovnání | Jak |
  |---|---|
  | na střed | bez `ESC s` |
  | vlevo | `ESC s` = 24 |
  | vpravo k okraji 111 | `ESC s` = 112 − šířka |
  | vpravo až k okraji 134 | s `ESC s` to nejde |

  gBUSE1 počítá zarovnání v okně 0–134, takže na tomto panelu by text zarovnaný vpravo uřízl.
- Fungují: běžící text (`ESC t` … `VT`), inverze (`ESC i`), změna linky (`l120`, `l020`).

### 3.6 Testovací data
`nastroje/make_test.py` sestaví z fontů existujícího HEX malou databázi `test/TEST.hex`. Verze dat je „TEST“, obsahuje linky `010/020/120` a cíle `001`–`011` s různými formáty a pravítka `020`–`022`. Původní data vrátí příkaz `python nastroje/nahraj.py ..\pce_trolejbusy.hex`.

### 3.7 Přes palubní počítač BS100 **[KÓD]**
Interně BSLoader skládá všechny příkazy jako `[typ][LEN][…]`:

| Typ | Význam |
|---|---|
| `0x11` | IBIS povel bez odpovědi |
| `0x12` | IBIS povel s odpovědí (dotazy `a…`, `cT`, `g…`, `h`, `i`, `yS`) |
| `0x13` | hlásič BS200 |
| `0x10`, `0x09`… | povely pro samotné BS100 (např. `09 01 "    Load data    "` je text na displeji BS100) |

- **Přímé připojení k panelu:** typ a délka se odříznou. Text jde jako IBIS telegram (1200 Bd), binární data jako rámec z kap. 3.2.
- **Přes BS100 (115200 Bd):** jde celý buffer v binárním rámci a BS100 ho přepošle na sběrnici. BS100 se detekuje rámcem `08 D0 9F 06`, odpověď obsahuje „Hlasic“, pokud jde o BS200.

## 4. Formát dat panelu (HEX soubory z gBUSE)

Soubory `.hex` jsou standardní Intel HEX. Výsledný obraz se ukládá do FLASH od adresy 0.
**[OVĚŘENO]**: FLASH panelu na COM5 je bajt po bajtu shodná se souborem `pce_trolejbusy.hex` (19 182 B, 0 rozdílů).

Existují dvě rodiny formátů:

| Začátek souboru | Editor | Soubory | Fonty |
|---|---|---|---|
| `AB 00 …` | **gBUSE1** | `DISPA.hex`, `pce_trolejbusy.hex`, `ADledA/B` | výška 8 bodů, 1 B na sloupec – jednořádkové panely BS120 apod. |
| `57 00 …` | **gBUSE0** | `OSTRAVA`, `TRAM_PRAHA`, `IDS_BRNO_Adcel`, `ANTI_001`, `KOSICE PANEL1-3`, `BRNO AD*` | výška 10 bodů, 2 B na sloupec – vnější panely |
| `cT0D…` | **gBUSE2b** | `PerlaD/E` | jiný formát (Perla) |

Kapitoly 4.1–4.5 vycházejí z disassembly `GBUSE1.EXE` (funkce `0x491230`, která skládá obraz a zapisuje HEX) a jsou ověřené zpětným sestavením. Modul `busepanel.core.image` (dříve skript `gbuse1.py`) rozebere HEX soubor a znovu ho sestaví. U všech 4 souborů gBUSE1 (`pce_trolejbusy`, oba `DISPA`, `KOSICE/DISPA`) je výsledek **znak po znaku shodný s originálem** **[OVĚŘENO]**.

### 4.1 Hlavička gBUSE1 (0x00–0x3F)
gBUSE1 má dvě varianty podle volby „FW“ v Konfiguraci panelu: FW 1.xx má magic `0xAA` a jiné rozložení, FW 2.00/2.06 má magic `0xAB`. Popsaná je varianta `0xAB`.

| Offset | Příklad (pce) | Význam (pole v Konfiguraci panelu) |
|---|---|---|
| 00 | `AB` | magic FW 2.x |
| 01 | `00` | – |
| 02 | `86` | **Délka** panelu − 1 (134 → 135 sloupců) |
| 03 | `07` | povolené povely, bity 0–7: `l, z, x, xA, xB, zC, zD, v` |
| 04 | `90` | povolené povely, bity 0–7: `zI, zM, zN, aA, u, e, xC, a` |
| 05 | `81` | `0x80` + bit0 `aV`, bit1 `lA` |
| 06 | `E3` | předvolba **Font** (`E0`+n) |
| 07 | `01` | předvolba **Mezera** |
| 08 | `0A` | předvolba **Interval** v desetinách s (1,0 s) |
| 09/0B/0D/0F/11 | | ukazatele BE: **FNT, LIN, CIL (3místné), CIL (4místné, s povelem zC), ZST** |
| 13/15/17/19/1B | | ukazatele BE: **MIM, CYK, DOP, kódová tabulka, formátovací řetězce** |
| 1D | `01` | zobrazování tarifního pásma (1–3 čísla) |
| 1E | `0A` | zpoždění cyklu |
| 1F | `01` | typ panelu (ComboBoxPanel) |
| 2A–39 | `NOVá` + CR | **verze dat**: 16 znaků doplněných CR, převedených kódovou tabulkou (vrací ji povel `aV`) |
| 3F | `FF` | – |

Prázdná sekce má ukazatel `00 3F`. Ten míří na bajt 0x3F = `FF`, tedy na prázdný seznam.
Panel tedy podle bajtů 03–05 ví, které IBIS povely má přijímat. U `pce_trolejbusy` jsou to `l, z, x, u, a, aV`.

### 4.2 Rozložení souboru
- Bloky se zapisují v pořadí **CYK, DOP, FNT, MIM, LIN, CIL, ZST**. Každý začíná na adrese zarovnané na 0x20.
- Každá sekce má titulek `"XXX: gBUSE1 - 1.00"` (18 B, bez doplnění), data začínají na další hranici 0x20 a ukazatel v hlavičce míří na data.
- Intel HEX: záznamy max. 0x20 B, CRLF, velká písmena. Mezery mezi bloky se nezapisují a po nahrání obsahují `FF`.
- Pro obraz nad 64 KB se používá záznam `:02000002…` (segment), konec je `:00000001FF`.
- Sekce dat: `FNT` (fonty), `LIN` (linky, povel `l`), `CIL` (cíle, `z` / `zC`), `ZST` (zastávky), `MIM` (mimořádné informace), `CYK` (cykly), `DOP` (doplňkové texty). Titulky s verzí 1.12 jsou v souborech Brno ADled.

### 4.3 Sekce FNT – fonty a kódová tabulka
```
{ [E0+n] [délka 16 bit BE] { [kód znaku] [počet bajtů] [výška] [sloupce…] }… }…
FF 00
[kódová tabulka 160 B]
```
- Sloupec je 1 bajt, **MSB = horní bod**, bit 0 = dolní.
- V `pce_trolejbusy` jsou fonty E0–E3 a EF (EF je jeden symbol, šipka 28 sloupců).
- **Kódová tabulka**: pro kódy panelu 0x20–0xBF udává odpovídající znak Windows‑1250 (0 = nepoužito). Od 0x80 je pořadí Kamenických (Č ü é ď ä Ď Ť č ě Ě …). gBUSE podle ní převádí text z Windows do kódů panelu.

### 4.4 Záznam textu (LIN/CIL/ZST/…)
```
KLÍČ(ASCII číslice)  DÉLKA(16 bit LE)  OBSAH(DÉLKA bajtů)   …   FF
```
- Klíč má 3 číslice. Ve 4 číslicích je u ZST a u cílů s povelem `zC`.
- Číslo odpovídá IBIS povelu: `l010` → záznam LIN `010`, `z001` → CIL `001` **[OVĚŘENO na panelu]**.
- Seznam končí záznamem `999` / `0999` a za ním následuje `FF`.

Obsah skládá editor cílů (`GBUSE1.EXE` 0x44BB44) takto:
```
E0+font  B0+mezera  [ESC t 11h]  [ESC i]  [ESC s (10h+sloupec)]  TEXT  [ESC i]  [VT]  CR
```

| Bajty | Význam | Tlačítko v editoru |
|---|---|---|
| `E0–EF` | font 0–15 | SpinEdit Font |
| `B0–BF` | **mezera mezi znaky** 0–15 bodů (v MNEMO `m<n>`) | M−/M+ |
| `1B 74 n` | ESC t – **běžící text** (editor vkládá n = 0x11) | BĚŽÍ |
| `0B` (VT) | konec běžícího úseku | BĚŽÍ |
| `1B 69` | ESC i – **inverze** zap/vyp | INV |
| `1B 73 n` | ESC s – **počáteční sloupec** n−0x10 (zarovnání vlevo/vpravo počítá editor) | L / P / LD / PD |
| bez ESC s | panel text **vycentruje** | C („I“) |
| `1B 61/6C/70/7A n`, `1B 62` | ESC a / l / p / z s parametrem, ESC b – editor je umí zobrazit, význam neznámý (používají je cykly) | – |
| `0D` | konec textu | |
| text | kódy panelu podle kódové tabulky (Kamenických) | |

Příklad z panelu:
```
CIL 001: <F3><B2>LÁZNĚ BOHDANEČ<CR>                   font 3, mezera 2, na střed
CIL 010: <F3><B1><ESC t 11h>LÁZNĚ BOHDANEČ - jede …<VT><CR>   font 3, mezera 1, běžící text
```

### 4.5 Konfigurace a soubory editoru gBUSE1
gBUSE si drží data v textových souborech a HEX skládá až při uložení:

| Soubor | Obsah |
|---|---|
| `*.lin` | linky |
| `*.cil` | cíle |
| `*.zst` | zastávky |
| `*.mim` | mimořádné informace |
| `*.dop` | doplňkové texty |
| `*.cyk` | cykly |
| `*.fnt` | fonty |
| `*.kod` | kódová tabulka (tlačítka „vlastní“ / „BS100“ / „žádná“) |
| `*.aL`, `*.LA` | formátovací řetězce |

Do `*.lin`, `*.mim`, `*.dop` a `*.cyk` umí gBUSE i exportovat HEX.
Cykly (CYK) popisují střídání informací. Volby efektů pro začátek zobrazení jsou: okamžité rozsvícení, přiběhnutí zprava, nasunutí zdola, nasunutí zhora, trvale běžící text. Pro konec: zhasnutí, vyběhnutí doleva, vysunutí nahoru, vysunutí dolů, ponechání.

### 4.6 Formát gBUSE0 (vnější panely, `57 00`) **[KÓD + OVĚŘENO na souborech]**
Funkce `GBUSE0.EXE` 0x48E400, manuál `PRG/gBUSE0/CZ/gBUSE0.doc` (ekselcom-pixel-master.zip). Stará varianta má
magic `0x55`. Implementace: [`busepanel/gbuse0/`](../busepanel/gbuse0) (čtení databáze + interpret záznamu).
Ověřeno vykreslením záznamů z BRNO ADcel0/ADbok4/ADzad7, OSTRAVA, TRAM_PRAHA a KOSICE PANEL1–3.

Hlavička má 0x50 B (vícebajtová čísla jsou big-endian):

| Offset | Význam |
|---|---|
| 00 | `57` |
| 01–04 | pole **linky**: řádek od, řádek do (počítáno **zdola**), sloupec od, sloupec do |
| 05–08 | pole **cíle** (stejně) |
| 09–0C | bitové masky povolených povelů (přiřazení bitů neznámé) |
| 0D–10 | předvolby linky: font (`E0`+n), mezera (`B0`+n), posun (`C0`+n), interval v 0,1 s |
| 11–14 | předvolby cíle |
| 15 / 17 / 19 / 1B | ukazatele **FNT, LIN, CIL, CIL4** (prázdný = `00 4F`) |
| 1F–2E | verze dat (text) |
| 2F–32 / 33–36 | pole **nácestných zastávek** (DRU) a jeho předvolby – jen u panelů, které ho mají (Brno boční) |
| 37 | ukazatel **DRU** |
| 4F | `FF` |

- Klíče záznamů: LIN a CIL třímístné (`l123`, `z123`), CIL4 a DRU čtyřmístné (`zC1234`, `zD1234…`).
- Fonty mají stejný blokový formát jako gBUSE1 (4.3). Znak je vysoký až 28 bodů, sloupec má ceil(výška/8) bajtů,
  nejvyšší bit prvního bajtu je horní bod. (OSTRAVA má 10bodové fonty – odtud dřívější „2 B na sloupec“.)
- Počet sloupců panelu = nejvyšší sloupec ze **všech** polí + 1 (zadní panel ADzad7 má jen pole linky, 28 sloupců).

**Řídicí kódy v záznamu** (FW úrovně podle manuálu: 1.6, 2.11, 2.13, 2.15, 3.08, 3.18):

| Kód | Význam |
|---|---|
| `E0`+n / `B0`+n / `C0`+n | font / mezera mezi znaky / posun dolů o n řádků |
| `ESC l n`, `ESC p n`, `ESC h n`, `ESC d n` | okno: levý, pravý sloupec, horní, dolní řádek; hodnota n−0x0F, číslováno od 1, sloupce zleva, řádky **zdola** |
| `ESC s n` | počáteční sloupec textu (jinak se text centruje v okně) |
| `ESC c` / `ESC i` / `ESC o` / `ESC x` | okno zpět na celé pole / invertovat okno / smazat / překrýt bez mazání (3.08) |
| `ESC b` | dokud záznam běží, ostatní telegramy se ignorují |
| `ESC w` | potlačí zobrazení nácestných zastávek (2.15) |
| `ESC z n` | interval stránky **[ODHAD]** |
| `FF` | formátování od začátku |
| `LF` | první LF jen formátuje (víc řádků v jednom snímku), každé další LF **zobrazí snímek** |
| `VT` | zobrazí snímek a cyklí od snímku po prvním LF **[OVĚŘENO na Košicích – 3 snímky]** |
| `CR` | zobrazí snímek a skončí |

Formátování maže jen pás řádků, který text zabírá (ne celé pole). Povely panelu: `l`, `z`, `zC`, `zD` (až 10
nácestných, 2.13), `zN` (nácestné textem), `zI` / `zA` / `aA` (volný text s automatickým formátováním, 3.08;
dvouřádkový s LF od 3.18), `kA`.

Obsah panelu: 158 linek, 245 cílů (Pardubice) a 230 zastávek. Zastávky jsou košické (Kavečany…), zřejmě zbytek starší databáze.

## 5. Firmware panelu
- Programová paměť (`0x08`): 32 KB, obsazeno 0x0000–0x32F1, procesor 8051 (reset vektor `LJMP 0x089F`)
- Identifikace: `DISPLEJ  V2.06 Ing.Jiri Babka Blansko  3.02.2000`, interní font `DISPLEJN.FNT 28.06.96`
- Výpisy jsou ve složce `vypis_panelu/`
- Rozbor zpracování IBIS povelů je v kapitole 9.

## 6. MyFaPixel (ekselcom-pixel-master.zip)
Nedokončený projekt v C#/WPF (VALDS & EKSELCOM, Riga) pro **jiný, novější** displej 128×8.

**Hodí se:**
- editor fontů a stránek, náhled
- výpočet CRC-8 (tabulka Dallas/Maxim)

**S BUSE není kompatibilní:**
- Formát databáze je vlastní: FAT tabulky, 32bitové adresy, fonty ve formátu `[šířka][výška][typ][hlavička][ascii]` po 256 znacích.
- Paketování je jiné: `[L][0x4F][DL][C][data][CRC8]` po 128 B.
- **Komunikace po sériové lince v projektu chybí úplně.** Vzniká jen soubor k nahrání.

Pro BUSE panely se tedy dá převzít maximálně GUI a editor fontů. Formát a protokol je potřeba napsat podle tohoto dokumentu.

## 7. Nástroje (`nastroje/`)
| Soubor | Účel |
|---|---|
| `buse.py` | IBIS kódování a dotaz, binární rámec, třída `Service` (vstup/čtení/výstup). Zapisovací příkazy záměrně neobsahuje. |
| `hex2bin.py` | převod Intel HEX na binární obraz |
| `gbuse_dump.py` | výpis hlavičky, sekcí, fontů a textů z HEX gBUSE1 (`N=počet` záznamů na sekci, `-v` vykreslí znaky) |
| `nahraj.py` | **nahrání HEX do panelu** (smaže, zapíše a zkontroluje). `python nahraj.py soubor.hex COM5 10` |
| `make_test.py` | sestavení testovací databáze s různými formáty textu |
| `d8051.py` | disassembler 8051 pro rozbor firmwaru: `python d8051.py eprom.bin start konec` |
| `gbuse1.py` | parser a builder obrazu gBUSE1 a zápis Intel HEX přesně jako gBUSE1. Spuštění `python gbuse1.py soubor.hex [vystup.hex]` vypíše konfiguraci a ověří round-trip. |

Spuštění: `python nastroje/buse.py COM5` provede sken adres povelem `aV`.

Ověřené IBIS povely na panelu: `l010` + `z001` → panel zobrazí „1 LÁZNĚ BOHDANEČ“ **[OVĚŘENO]**.

## 8. Otevřené otázky
- Význam `ESC a/l/p/z/b` a parametru `n` u `ESC t`. Řešení: rozbor editoru cyklů nebo firmwaru 8051.
- gBUSE0: přiřazení bitů masek povelů (09–0C), parametr `ESC z`, oddělovač v `zN`; builder (zápis HEX) zatím není.
- gBUSE1 FW 1.xx (magic `0xAA`) a soubory 1.12 se sekcemi CYK/DOP (Brno ADled) – kód je zmapovaný, round-trip zatím nebyl zkoušen.
- Ověřit na panelu přepínače `x99xx` a volný text (kap. 9.3).
- Proč se nezobrazilo `zC` (hledání záznamu, rutina 0x0C23).

## 9. Rozbor firmwaru BS120 (8051) – IBIS povely **[KÓD, firmware]**
Disassembler: `analyza/nastroje/d8051.py`.

**Důležité adresy ve firmwaru:**

| Adresa | Co tam je |
|---|---|
| 0x180A | rozdělení povelů podle prvního písmene |
| 0x0600 | přijatý telegram |
| 0x093C | hlavní smyčka |
| registr `45h` | stav panelu |

**Formáty povelů:**

| Povel | Formát | Poznámka |
|---|---|---|
| `l` | 3 číslice | |
| `z` | **přesně 3 číslice** | |
| `zC` | 4 číslice | zobrazení zatím nefunguje, podezření na hledání záznamu přes ukazatel 0x0F (rutina 0x0C23) |
| `x` | **přesně 4 číslice** | `x001` panel zahodí. Stejné číslo podruhé panel ignoruje (porovná ho s 0x1A88). |

**Každý povel má svůj vypínač** v bitech hlavičky (bajty 3–5, pořadí odpovídá kap. 4.1).

**Volný text** se ukládá do XRAM panelu:

| Povel | Uloží do |
|---|---|
| `v<text>` | 0x0800 |
| `zI<p><text>` | 0x0900 |
| `zN<p><text>` | 0x0A00 |
| `zM<p><text>` | 0x0B00 |

`<p>` je jeden znak parametru. Stejný text podruhé panel nepřekreslí. `zA` ani `aA` firmware nezná.

**Režim zastávek:** po `x` je stav panelu 1 a smyčka na 0x0AEA zpracuje jen další `x`. Ostatní povely čekají. Čísla `99xx` (0x0B1A–0x0BF5) jsou přepínače zobrazení:

| Povel | Zobrazí |
|---|---|
| `x9995` | cíl (`z`) |
| `x9996` | text `v` |
| `x9990` | text `zM` |
| `x9991` | `xA` |
| `x9992` | `zC` |
| `x9987` | text `zN` |
| `x9989` | `xB` |

Význam `x9993/9994/9997–9999` zatím není známý.

Na panelu zatím ověřeno:
- `x0001` a `x0002` zobrazí zastávky,
- `x0000` panel z režimu zastávek nevyvede,
- `zC0001` nezobrazilo nic.

### 9.1 Upřesnění (druhý, nezávislý rozbor firmwaru)
Stav panelu je v `45h`. Spodní polovina se při startu nastaví z bajtu 0x1F hlavičky („typ panelu“). Při 1 hlavní smyčka (0x0AEA) bere `x` jako přepínač režimu.

- Po startu přejde panel do stavu 0x11 (linka + cíl).
- Běžné číslo zastávky nastaví stav 0x01 = jen zastávka. V něm panel bere jen další `x`.

Přepínače (tabulka na 0x0B55):

| Povel | Stav | Zobrazení |
|---|---|---|
| **`x9999`** | 11 | **linka + cíl (běžný provoz)** |
| `x9998` | 21 | ? (0F0F) |
| `x9997` | 31 | linka + text `v` |
| `x9996` | 41 | volný text `v` |
| `x9995` | 51 | jen cíl |
| `x9994` | 61 | linka + text `zI` |
| `x9993` | 71 | linka + `zC` |
| `x9992` | 81 | `zC` |
| `x9991` | 91 | `xA` |
| `x9990` | A1 | text `zM` |
| `x9989`–`x9986` | B1–E1 | `xB`, `zD`, `zN`, text 0x0700 |

Po přepnutí je potřeba poslat `z`/`l` s **jiným** číslem než naposledy, jinak panel nepřekreslí. Tento oddíl má přednost před tabulkou výše.

### 9.2 Jak se rozbor dělá
1. Program panelu se stáhne servisním příkazem `0x08` (32 KB, `vypis_panelu/BS120_adr10_eprom.bin`).
2. Disassembler `nastroje/d8051.py`, který převádí bajty na instrukce 8051: `python d8051.py eprom.bin 180A 1860`.
3. Vstupní bod se najde podle porovnání s písmeny povelů. Hledá se bajt `B4` (instrukce `CJNE A,#…`) následovaný kódem písmena, např. `B4 78` = `x`.
4. Pak se sledují skoky:
   - obslužná rutina povelu nastaví bit události (`SETB bit1x`),
   - hlavní smyčka (0x093C) ho otestuje a smaže (`JBC`),
   - pak zavolá zobrazení s ukazatelem na text v XRAM nebo na sekci v hlavičce.

**Kontrola délky telegramu** (rutina 0x1A0E): v 0x058A je délka telegramu včetně CR. Panel ověří, že jde o samé číslice (0x1A7A) a že délka přesně odpovídá. Proto `x` musí mít 4 číslice a `z` 3. Číslo se pak porovná s naposledy uloženým (0x1A88) a když je stejné, panel nic nepřekreslí.

**Proč první testy (kap. 3.5) dopadly špatně:** po `x0001` přešel panel do stavu „jen zastávka“, kde čeká jen na další `x`. Všechny následující `z`, `v`, `zI`, `zM` proto čekaly a `x0000` je obyčejné číslo zastávky.

### 9.3 Doporučený test (po vypnutí a zapnutí panelu, data s povolenými povely)
1. `l010`, `z001` – kontrola běžného provozu,
2. `vAHOJ SVETE` – volný text,
3. `zI0VOLNY TEXT` – volný text s parametrem,
4. `x0001` – zastávka,
5. `x9999`, pak `z002` – návrat na linku a cíl (jiné číslo cíle než naposledy),
6. `vJINY TEXT`, pak `x9996` – volný text přes přepínač.

### 9.4 Ověřeno na panelu (BS120, data EXP)

| Povel | Výsledek |
|---|---|
| `v<text>` | sám nic nezobrazí, text se jen uloží |
| `x9996` | zobrazí text z `v` |
| `x9999` | vrátí běžný provoz (linka + cíl); pak `z` s novým číslem |
| `x9997` | zobrazí linku + text z `v` |
| `zI0<text>` | sám nic nezobrazí |

U volného textu dále funguje:
- **Češtinu** je potřeba kódovat kódovou tabulkou panelu (Kamenický), bajty ≥ 0x80 jako `0E xx` v IBIS.
- **Font a mezera** ve volném textu fungují (`E0+n`, `B0+n` na začátku).
- **Nový text** po `v` + `x9996` se zobrazí hned. Na tom běží hodiny a odjezdová tabule v BUSE Studiu.

### 9.5 Čísla linek a restart po nahrání **[OVĚŘENO na BS120]**
- **`l<NNN>` používá jen poslední dvě číslice.** Hledá se záznam LIN `0NN`.
  - `l607` → záznam 007, `l123` → 023, `l100` → 000 (prázdný).
  - Pole linky má tedy jen 100 míst. Linky nad 99 je potřeba uložit do volného místa; univerzální databáze BUSE Studia to dělá mapou `line_map`, například 607 → 099.
- **Když záznam LIN neexistuje**, panel zobrazí poslední dvě číslice z povelu bez úvodní nuly.
- **Po nahrání nových dat je nutné panel vypnout a zapnout.**
  - Rejstřík linek si firmware načítá při startu. Bez restartu `l099` ukazovalo „99“, po restartu správně „607“.
  - Cíle (`z`) a volný text se z nových dat projeví i bez restartu.

### 9.6 Znaky, fonty a ikony ve volném textu **[firmware + OVĚŘENO na BS120]**
Z EPROM `BS120_adr10_eprom.bin`:

- **Hledání znaku** (0x12D8): je-li na adrese 0 ve FLASH bajt `AB` (nahraná data), bere se ukazatel na fonty z FLASH (0x0009), jinak z vestavěných fontů v EPROM (0x2009 → fonty 0–3 od 0x2060). Ve fontu `E0+n` (aktuální font) se znaky procházejí po řadě a hledání **končí u prvního většího kódu** (0x1329) → znaky ve fontu musí být seřazené vzestupně. `FF` končí font.
- **Třídění bajtů textu** (0x14E7): `1B` ESC, `0A`/`0D` konec řádku/textu, `0B` konec běžícího textu, `0E x` = `x | 80h` (kromě `0E 0D`), `20–AF` **znak**, `B0–BF` mezera, **`C0–DF` příkaz bez účinku – bajt se zahodí**, `E0–EF` font, `F0–FE` **znak**.
- Samostatný font s piktogramy (např. `EE`) panel ve volném textu nezobrazí, protože text je vždy v aktuálním fontu.

Ověřeno na panelu (volný text `v`, x9997):

| Kód | Výsledek |
|---|---|
| 0x80–0xA9 (Kamenických) kromě 0x8B | OK |
| **0x8B (Í)** | znak se vykreslí, ale **text za ním skončí** → Í se v univerzální databázi přesouvá na **0x40** (`@`, v originále prázdné; v gBUSE se Í psalo jako `@`) |
| 0xAA, 0xAB, 0xAE, 0xF0, 0xFD, 0xFE (nové znaky v nahrané databázi) | OK |
| 0xAF, 0xF1–0xF5 (originální piktogramy gBUSE) | OK |
| 0xC0–0xDF | nic (zahozeno) |

BUSE Studio proto dává ikony do každého textového fontu na kódy 0xAA–0xAE, 0xF0, 0xF5–0xFE (`core/icons.py`) a `{icon:…}` posílá jako jediný znak.
Bitmapy metra, vlaku, letadla, šipky, zvonku, přívozu, výluky, ČD a rohů stylizovaného písmene metra jsou převzaté
ze standardu PID (SK bus, příloha 2, „seznam požadovaných znaků“, sloupec vnitřní panely, kódy 0x3C, 0x7E, 0x5C,
0x3E, 0xAA, 0x8A, 0x24, 0x25, 0x5B/0x5D). PID tam má piktogramy přímo na místě znaků; BUSE Studio je kvůli
zachování kódové tabulky dává na volné kódy výše.

## 10. Vnější panely v simulaci (BS 210, gBUSE0) **[ODHAD]**
Skutečný vnější panel ani palubní počítač BS100 k dispozici nemáme. Emulátor v
[`busepanel/peripherals/exterior.py`](../busepanel/peripherals/exterior.py) proto vychází z kap. 4.6 a z manuálů
gBUSE0 a dBUSE. Co je odhad:

- **Varianty bez databáze** ([`exterior.yaml`](../busepanel/peripherals/exterior.yaml)): v1 čelní 140×19, v2 boční
  s číslem 110×19, v4 zadní 28×19. Pole linky má 28 sloupců. Fonty jsou originální fonty gBUSE0 0–12
  ([`gbuse0_font.yaml`](../busepanel/peripherals/gbuse0_font.yaml)), převzaté z dat gBUSE0. Soubory `*.fnt`
  editoru gBUSE0 k dispozici nejsou.
- **Řádek nácestných zastávek**: `stop_field: top` (10 řádků nahoře) nebo `bottom` (8 řádků dole, cíl nad ním).
- **Číslo linky**: bez záznamu LIN se vykreslí číslo podle `line_map` univerzální databáze (`l098` → „607“). Klíč
  je stejný, jaký posílá BS120, takže se oba panely na jedné lince nepřou.
- **Volný text** `zI`: automatické formátování vybere největší font, který se vejde, s mezerou 1, případně 0. Když
  text nevejde, rozdělí ho na dva řádky u mezery, a když nejsou místo ani na dva řádky, použije nejužší font na
  jednom řádku. Font se nepoužije, když v něm některý znak chybí, nebo když jeho písmena nesedí na jedné účaří
  (font 9 má malá písmena jako horní index).
- **Font ze skladby textů**: kódy fontu a mezery (`E0`+n, `B0`+n) na začátku textu `zI` nebo položky `zN` určí
  font místo automatického formátování.
- **Nácestné** `zN`: zastávky odděluje `|`. Střídají se po době stránky ze sekvence `via`. Jsou to zastávky
  označené `via: true`, které vozidlo ještě nemá za sebou.
- **Sdílená linka**: IBIS je vysílací linka. Telegram, který už na lince je, ovladač vnějšího panelu nepošle znovu.
  Všechny vnější panely na jednom COM portu tak ukazují stejnou linku a cíl, stejně jako ve voze s BS100.
  Ovládání BS120 (`v` + `x9996/x9997`) se nemění.
