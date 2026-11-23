# Testovací pracovní složka

Ukázkové linky (`trasy/`, včetně linky se směry a kurzy `linka3_kurzy.yaml`) a vozidla (`vozidla/` –
podsložky se v UI zobrazí jako skupiny: `test/` jen náhledy v počítači, `jednoradek/` s fyzickým BS120 na
COM portu). Databáze panelu tu není, vytvoří ji `busepanel quickstart` nebo tlačítko „Sestavit“.

Spuštění nad kopií (aby se repozitář neměnil):

    cp -r tests/data/pracovni_slozka /tmp/busepanel-test
    busepanel quickstart --workdir /tmp/busepanel-test
    busepanel serve --workdir /tmp/busepanel-test

Testy (`tests/test_peripherals.py`) kontrolují, že se všechny linky a vozidla dají načíst.
