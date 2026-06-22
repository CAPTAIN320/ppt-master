import os

files = ["03_deloitte.svg", "04_pwc.svg", "05_ey.svg", "06_kpmg.svg"]
for f in files:
    path = f"projects/big_4_comparison_ppt169_20260622/svg_output/{f}"
    with open(path, "r") as file:
        content = file.read()

    content = content.replace("Audit & Assurance", "Audit & Assurance")
    content = content.replace("Price Waterhouse & Coopers & Lybrand", "Price Waterhouse & Coopers & Lybrand")
    content = content.replace("Ernst & Young", "Ernst & Young")
    content = content.replace("Ernst & Whinney & Arthur Young & Co.", "Ernst & Whinney & Arthur Young & Co.")
    content = content.replace("PMI & KMG", "PMI & KMG")
    content = content.replace("Tax & Legal", "Tax & Legal")

    with open(path, "w") as file:
        file.write(content)
    print(f"Updated {f}")
