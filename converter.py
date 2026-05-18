def convert(value, from_base, to_base):
    decimal = int(value, from_base)
    if to_base == 10:
        return str(decimal)
    elif to_base == 2:
        return bin(decimal)[2:]
    elif to_base == 16:
        return hex(decimal)[2:].upper()

BASES = {
    "1": (10, "Dezimal"),
    "2": (2,  "Binär"),
    "3": (16, "Hexadezimal"),
}

def main():
    print("=== Zahlensystem-Konverter ===")
    while True:
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
        value = input(f"{from_name}-Zahl eingeben: ").strip()

        try:
            int(value, from_base)
        except ValueError:
            print(f"Ungültige {from_name}-Zahl: '{value}'")
            continue

        print("\nIn welches Format umwandeln?")
        for k, (_, name) in BASES.items():
            print(f"  {k}) {name}")

        choice2 = input("> ").strip()
        if choice2 not in BASES:
            print("Ungültige Auswahl.")
            continue

        to_base, to_name = BASES[choice2]
        result = convert(value, from_base, to_base)
        print(f"\n  {value} ({from_name}) = {result} ({to_name})")

if __name__ == "__main__":
    main()
