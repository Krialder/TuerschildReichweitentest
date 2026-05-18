# Zahlensystem-Konverter als Klasse
class Umwandeln:

    # Verfügbare Zahlensysteme: Menütaste -> (Basis, Anzeigename)
    BASEN = {
        "1": (10, "Dezimal"),
        "2": (2,  "Binär"),
        "3": (16, "Hexadezimal"),
    }

    def __init__(self):
        # Speichert das Ergebnis der letzten Umwandlung
        self.letztes_ergebnis = None

    def prüfen(self, eingabe, art):
        # Prüft ob die Eingabe zum gewählten Zahlensystem passt
        geprüft = True
        try:
            int(eingabe, art)
        except ValueError:
            geprüft = False
        return geprüft

    def umwandeln(self, wert, von_basis, zu_basis):
        # Erst in Dezimal, dann ins Zielformat umrechnen
        dezimal = int(wert, von_basis)
        if zu_basis == 10:
            return str(dezimal)
        elif zu_basis == 2:
            return bin(dezimal)[2:]        # [2:] entfernt "0b"-Präfix
        elif zu_basis == 16:
            return hex(dezimal)[2:].upper() # [2:] entfernt "0x"-Präfix

    def menü_zeigen(self, titel):
        print(f"\n{titel}")
        for taste, (_, name) in self.BASEN.items():
            print(f"  {taste}) {name}")

    def starten(self):
        print("=== Zahlensystem-Konverter ===")

        # Läuft so oft wie man will – beenden mit "q"
        while True:
            self.menü_zeigen("Von welchem Format?")
            print("  q) Beenden")

            wahl1 = input("> ").strip().lower()
            if wahl1 == "q":
                print("Tschüss!")
                break
            if wahl1 not in self.BASEN:
                print("Ungültige Auswahl.")
                continue

            von_basis, von_name = self.BASEN[wahl1]

            # Eingabe einlesen und prüfen
            eingabe = input(f"{von_name}-Zahl eingeben: ").strip()
            if not self.prüfen(eingabe, von_basis):
                print(f"Ungültige {von_name}-Zahl: '{eingabe}'")
                continue

            self.menü_zeigen("In welches Format umwandeln?")
            wahl2 = input("> ").strip()
            if wahl2 not in self.BASEN:
                print("Ungültige Auswahl.")
                continue

            zu_basis, zu_name = self.BASEN[wahl2]

            # Umwandlung durchführen und Ergebnis speichern + ausgeben
            self.letztes_ergebnis = self.umwandeln(eingabe, von_basis, zu_basis)
            print(f"\n  {eingabe} ({von_name}) = {self.letztes_ergebnis} ({zu_name})")


# Startet das Programm nur wenn direkt ausgeführt (nicht bei Import)
if __name__ == "__main__":
    konverter = Umwandeln()
    konverter.starten()
