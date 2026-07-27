import gspread

gc = gspread.service_account(filename="credentials.json")

NOMBRE_HOJA = "Reporte de Ordenes con Fraude"

try:
    sheet = gc.open(NOMBRE_HOJA).sheet1

    filas = sheet.get_all_values()
    
    if not filas:
        print("La hoja está completamente vacía.")
    else:

        encabezados = filas[0]
        datos_filas = filas[1:]

        registros = []
        for fila in datos_filas:
            registro = {}
            for i, header in enumerate(encabezados):
                if header.strip():

                    registro[header] = fila[i] if i < len(fila) else ""
            registros.append(registro)

        print("--------------------------------------------------")
        print("✅ ¡CONEXIÓN Y LECTURA EXITOSA!")
        print(f"Total de registros leídos: {len(registros)}")
        print("--------------------------------------------------")
        print("Ejemplo de la primera fila leída:")
        print(registros[0])
        print("--------------------------------------------------")

except Exception as e:
    print("--------------------------------------------------")
    print("❌ ERROR DE CONEXIÓN:")
    print(e)
    print("--------------------------------------------------")

    