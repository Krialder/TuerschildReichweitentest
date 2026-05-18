# Wandelt eine Zahl von einem Zahlensystem in ein anderes um
def convert(value, from_base, to_base):
    # Erst in Dezimal umrechnen, dann ins Zielformat
    decimal = int(value, from_base)
    if to_base == 10:
        return str(decimal)
    elif to_base == 2:
        return bin(decimal)[2:]   # [2:] entfernt das "0b"-Präfix
    elif to_base == 16:
        return hex(decimal)[2:].upper()  # [2:] entfernt "0x", upper() macht Großbuchstaben

# Menü-Optionen: Taste -> (Basis als Zahl, Anzeigename)
BASES = {
    "1": (10, "Dezimal"),
    "2": (2,  "Binär"),
    "3": (16, "Hexadezimal"),
}

def main():
    print("=== Zahlensystem-Konverter ===")

    # Hauptschleife – läuft bis der Nutzer "q" eingibt
    while True:
        # Quellformat auswählen
        print("\nVon welchem Format?")
        for k, (_, name) in BASES.items():
            print(f"  {k}) {name}")
        print("  q) Beenden")

        choice = input("> ").strip().lower()
        if choice == "q":
            print("Tschüss!")
            break
        if choice not in BASES:
            print("Ungültige Auswahl.")
            continue

        from_base, from_name = BASES[choice]

        # Zahl einlesen und auf Gültigkeit prüfen
        value = input(f"{from_name}-Zahl eingeben: ").strip()
        try:
            int(value, from_base)  # Wirft ValueError bei ungültiger Eingabe
        except ValueError:
            print(f"Ungültige {from_name}-Zahl: '{value}'")
            continue

        # Zielformat auswählen
        print("\nIn welches Format umwandeln?")
        for k, (_, name) in BASES.items():
            print(f"  {k}) {name}")

        choice2 = input("> ").strip()
        if choice2 not in BASES:
            print("Ungültige Auswahl.")
            continue

        # Umwandlung durchführen und Ergebnis ausgeben
        to_base, to_name = BASES[choice2]
        result = convert(value, from_base, to_base)
        print(f"\n  {value} ({from_name}) = {result} ({to_name})")

# Startet das Programm nur wenn direkt ausgeführt (nicht bei Import)
if __name__ == "__main__":
    main()
