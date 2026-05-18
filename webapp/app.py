from flask import Flask, render_template, request

app = Flask(__name__)

BASES = {
    "decimal": 10,
    "binary":   2,
    "hex":     16,
}

def convert(value, from_base, to_base):
    decimal = int(value, from_base)
    if to_base == 10:
        return str(decimal)
    elif to_base == 2:
        return bin(decimal)[2:]
    elif to_base == 16:
        return hex(decimal)[2:].upper()

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    value = ""
    from_base = "decimal"
    to_base = "binary"

    if request.method == "POST":
        value = request.form.get("value", "").strip()
        from_base = request.form.get("from_base", "decimal")
        to_base = request.form.get("to_base", "binary")

        try:
            result = convert(value, BASES[from_base], BASES[to_base])
        except (ValueError, KeyError):
            error = f"Ungültige Eingabe für {from_base}: '{value}'"

    return render_template("index.html",
                           result=result,
                           error=error,
                           value=value,
                           from_base=from_base,
                           to_base=to_base)

if __name__ == "__main__":
    app.run(debug=True)
