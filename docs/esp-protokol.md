# Protokol server ↔ ESP (brána k panelu)

ESP (např. ESP32) je **průhledná brána**: přijme od serveru hotový IBIS telegram a pošle ho beze změny na sériovou linku k panelu.

Veškerou logiku dělá server:
- kódování textů a kódovou tabulku,
- kontrolní znak,
- přepínání režimů `x99xx`.

## Spojení
- ESP se připojí jako WebSocket klient na `ws://<server>:8080/ws/device/<id>`.
  - `<id>` je jméno panelu, například `celni`, `vnitrni-1` nebo `bus12-zadni`.
  - Stejné `<id>` se zadává v BUSE Studiu u panelu s výstupem „ESP (WebSocket)“.
- Po spadnutí spojení se má ESP znovu připojit, například každých 5 s.
- Seznam připojených zařízení je v BUSE Studiu v Loaderu (karta Připojená ESP zařízení), případně na `GET /api/devices`.

## Zprávy server → ESP
```json
{"type": "ibis", "data": "76414842...0d25", "text": "vAHOJ"}
```

| Pole | Význam |
|---|---|
| `data` | kompletní telegram v hex, už včetně `CR` a kontrolního znaku, bajty ≥ 0x80 už jsou escapované (`0E xx`). ESP ho jen zapíše na UART. |
| `text` | čitelná podoba, jen pro ladění |

Telegramy chodí v pořadí, v jakém se mají poslat. Mezi telegramy musí ESP udělat pauzu **≥ 200 ms**: panel potřebuje čas na zpracování. Server pauzy nedělá, posílá zprávy hned za sebou.

## Zprávy ESP → server (volitelné)
```json
{"type": "hello", "id": "celni", "fw": "0.1", "ip": "192.168.1.50"}
{"type": "rx", "data": "61564e4f56610d73"}
```
- `hello` pošle ESP po připojení.
- `rx` posílá všechno, co přišlo z panelu, například odpověď na `aV`.

## Sériová linka k panelu (ověřeno na BS120)

| Parametr | Hodnota |
|---|---|
| Rychlost | 1200 Bd |
| Formát | 7 datových bitů, sudá parita, 2 stop bity (7E2) |
| DTR | musí být neaktivní (0). Při aktivním DTR panel neodpovídá. |
| Převodník | sběrnice IBIS je otevřený kolektor nebo proudová smyčka, ESP potřebuje převodník TTL ↔ IBIS. Ověřený je USB převodník Prolific + převodník IBIS. |

## Které telegramy server posílá (BS120)

| Účel | Telegramy |
|---|---|
| linka + cíl z databáze | `x9999`, `lNNN`, `zNNN` |
| volný text | `v<text>`, `x9996` |
| linka + volný text | `lNNN`, `v<text>`, `x9997` |
| dotaz na verzi dat | `aV<adresa>` (odpověď přijde jako `rx`) |

Servisní režim (nahrávání dat) přes ESP zatím podporovaný není. Nahrávání jde jen z počítače přes COM port (modul Loader).
