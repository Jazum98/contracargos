import os
import io
import zipfile
from datetime import datetime, timedelta
from typing import Optional, List
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gspread

class LoginRequest(BaseModel):
    username: str
    password: str

class CrearUsuarioReq(BaseModel):
    username: str
    nombre: str
    email: str
    rol: str

class CambiarPasswordReq(BaseModel):
    user_id: int
    password_nueva: str



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Ruta de Secret Files en Render
RENDER_SECRET_PATH = "/etc/secrets/credentials.json"

# Si existe el archivo en Render lo usa; si no, usa la ruta local
if os.path.exists(RENDER_SECRET_PATH):
    GOOGLE_CREDENTIALS_FILE = RENDER_SECRET_PATH
else:
    GOOGLE_CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")

DATABASE_URL = os.getenv("DATABASE_URL")

DB_CONFIG = {
    "dbname": "sistema_contracargos",
    "user": "postgres",
    "password": "JosZum98!",
    "host": "localhost",
    "port": "5432"
}

def get_db_connection():
    try:
        # Si existe DATABASE_URL (estamos en Render), usa la URL de la nube
        if DATABASE_URL:
            # Render a veces entrega la URL iniciando con 'postgres://', corregimos a 'postgresql://'
            url = DATABASE_URL.replace("postgres://", "postgresql://", 1) if DATABASE_URL.startswith("postgres://") else DATABASE_URL
            conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
        else:
            # Si estamos ejecutando en local, usa la configuración DB_CONFIG tradicional
            conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error de conexión a la BD: {str(e)}")

class ContracargoCreate(BaseModel):
    pasarela: str
    no_idcontracargo: Optional[str] = None
    no_orden: str
    correo_cliente: str
    tarjeta_mascarada: str
    monto: float
    fecha_transaccion: str
    fecha_contracargo: str
    estado_orden: str
    autorizacion: str
    metodo_pago: Optional[str] = None
    tipo_contracargo: Optional[str] = None
    unidad_negocio: Optional[str] = None
    estado_ocadmin: Optional[str] = None
    observaciones: Optional[str] = None

class ContracargoUpdate(BaseModel):
    monto: Optional[float] = None
    p_and_l: Optional[str] = None  
    fecha_info_enviada: Optional[str] = None
    fecha_resolucion: Optional[str] = None
    resolucion: Optional[str] = None

   



app = FastAPI(title="API Contracargos BAC & Neonet")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static/evidencias", exist_ok=True)
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def calcular_p_and_l(fecha_str: str) -> str:
    dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    meses = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    return f"{meses[dt.month - 1]} {dt.year}"

@app.get("/")
def read_root():
    return FileResponse("index.html")

@app.post("/api/auth/login")
def login(datos: LoginRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT id, username, nombre, email, password, rol, requiere_cambio_password 
        FROM usuarios 
        WHERE (LOWER(username) = LOWER(%s) OR LOWER(email) = LOWER(%s));
    """
    cursor.execute(query, (datos.username, datos.username))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not user or user["password"] != datos.password:
        raise HTTPException(status_code=400, detail="Usuario/Correo o contraseña incorrectos")
        
    return {
        "status": "exito",
        "id": user["id"],
        "username": user["username"],
        "nombre": user["nombre"],
        "email": user["email"],
        "rol": user["rol"],
        "requiere_cambio_password": user["requiere_cambio_password"]
    }

@app.post("/api/usuarios/crear")
def crear_usuario(datos: CrearUsuarioReq):
    conn = get_db_connection()
    cursor = conn.cursor()
    password_temporal = "ClaveInicial123!"
    
    try:
        query = """
            INSERT INTO usuarios (username, nombre, email, password, rol, requiere_cambio_password)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            RETURNING id;
        """
        cursor.execute(query, (datos.username, datos.nombre, datos.email, password_temporal, datos.rol))
        conn.commit()
        cursor.close()
        conn.close()
        return {
            "status": "exito", 
            "mensaje": f"Usuario {datos.username} creado correctamente.",
            "password_temporal": password_temporal
        }
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail=f"El usuario o correo ya existe o hubo un error: {str(e)}")

@app.post("/api/usuarios/cambiar-password")
def cambiar_password(datos: CambiarPasswordReq):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "UPDATE usuarios SET password = %s, requiere_cambio_password = FALSE WHERE id = %s;"
    cursor.execute(query, (datos.password_nueva, datos.user_id))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "exito", "mensaje": "Contraseña actualizada con éxito"}

@app.post("/api/contracargos")
def crear_contracargo(data: ContracargoCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    p_and_l_calc = calcular_p_and_l(data.fecha_contracargo)
    
    query = """
        INSERT INTO contracargos (
            pasarela, no_idcontracargo, no_orden, correo_cliente, tarjeta_mascarada,
            monto, fecha_transaccion, fecha_contracargo, estado_orden, autorizacion,
            metodo_pago, tipo_contracargo, unidad_negocio, estado_ocadmin, observaciones, p_and_l
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *;
    """
    cursor.execute(query, (
        data.pasarela, data.no_idcontracargo, data.no_orden, data.correo_cliente, data.tarjeta_mascarada,
        data.monto, data.fecha_transaccion, data.fecha_contracargo, data.estado_orden, data.autorizacion,
        data.metodo_pago, data.tipo_contracargo, data.unidad_negocio, data.estado_ocadmin, data.observaciones, p_and_l_calc
    ))
    nuevo_registro = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()
    return nuevo_registro

@app.get("/api/contracargos")
def listar_contracargos():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM contracargos ORDER BY id DESC;")
    registros = cursor.fetchall()
    cursor.close()
    conn.close()
    return registros

@app.put("/api/contracargos/{id_registro}")
def actualizar_contracargo(id_registro: int, data: ContracargoUpdate):
    conn = get_db_connection()
    cursor = conn.cursor()

    updates = []
    values = []

    if data.monto is not None:
        updates.append("monto = %s")
        values.append(data.monto)
    if data.p_and_l is not None:  # <-- AGREGAR ESTE BLOQUE
        updates.append("p_and_l = %s")
        values.append(data.p_and_l)
    if data.fecha_info_enviada is not None:
        updates.append("fecha_info_enviada = %s")
        values.append(data.fecha_info_enviada if data.fecha_info_enviada else None)
    if data.fecha_resolucion is not None:
        updates.append("fecha_resolucion = %s")
        values.append(data.fecha_resolucion if data.fecha_resolucion else None)
    if data.resolucion is not None:
        updates.append("resolucion = %s")
        values.append(data.resolucion)

    if not updates:
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar")
        
    values.append(id_registro)
    query = f"UPDATE contracargos SET {', '.join(updates)} WHERE id = %s RETURNING *;"
    
    cursor.execute(query, tuple(values))
    registro = cursor.fetchone()
    
    if not registro:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Registro no encontrado")
        
    conn.commit()
    cursor.close()
    conn.close()
    return registro

@app.patch("/api/contracargos/{id_registro}/resolucion")
def cambiar_resolucion(id_registro: int, resolucion: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE contracargos SET resolucion = %s WHERE id = %s RETURNING id;", (resolucion, id_registro))
    registro = cursor.fetchone()
    
    if not registro:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Registro no encontrado")
        
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "ok", "resolucion": resolucion}

@app.post("/api/contracargos/{id_registro}/subir-evidencia")
async def subir_evidencia(id_registro: int, archivos: List[UploadFile] = File(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT archivo_evidencia FROM contracargos WHERE id = %s;", (id_registro,))
    registro = cursor.fetchone()
    
    if not registro:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    
    archivo_actual = registro["archivo_evidencia"]
    
   
    if len(archivos) == 1 and not archivo_actual:
        archivo = archivos[0]
        nombre_archivo = f"evidencia_{id_registro}_{archivo.filename}"
        ruta_guardado = f"static/evidencias/{nombre_archivo}"
        
        with open(ruta_guardado, "wb") as f:
            f.write(await archivo.read())
            
        cursor.execute("UPDATE contracargos SET archivo_evidencia = %s WHERE id = %s;", (nombre_archivo, id_registro))
        conn.commit()
        cursor.close()
        conn.close()
        return {"status": "éxito", "archivo": nombre_archivo}

   
    nombre_zip = f"evidencia_{id_registro}_paquete.zip"
    ruta_zip = f"static/evidencias/{nombre_zip}"
    
    
    if archivo_actual and not archivo_actual.endswith('.zip'):
        ruta_anterior = f"static/evidencias/{archivo_actual}"
        if os.path.exists(ruta_anterior):
            with zipfile.ZipFile(ruta_zip, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.write(ruta_anterior, arcname=archivo_actual)
            os.remove(ruta_anterior)

   
    modo = 'a' if os.path.exists(ruta_zip) else 'w'
    with zipfile.ZipFile(ruta_zip, modo, zipfile.ZIP_DEFLATED) as zip_file:
        for arc in archivos:
            contenido = await arc.read()
            zip_file.writestr(arc.filename, contenido)
            
    cursor.execute("UPDATE contracargos SET archivo_evidencia = %s WHERE id = %s;", (nombre_zip, id_registro))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "éxito", "archivo": nombre_zip}

@app.get("/api/contracargos/exportar-excel")
def exportar_excel(pasarela: Optional[str] = None, pnl: Optional[str] = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM contracargos WHERE 1=1"
    params = []
    if pasarela:
        query += " AND pasarela = %s"
        params.append(pasarela)
    if pnl:
        query += " AND p_and_l = %s"
        params.append(pnl)
        
    cursor.execute(query, tuple(params))
    registros = cursor.fetchall()
    cursor.close()
    conn.close()
    
    datos = []
    for r in registros:
        datos.append({
            "ID": r["id"],
            "Pasarela": r["pasarela"],
            "No. Caso": r["no_idcontracargo"] or "N/A",
            "No. Orden": r["no_orden"],
            "Correo Cliente": r["correo_cliente"],
            "Tarjeta Mascarada": r["tarjeta_mascarada"],
            "Monto": r["monto"],
            "Unidad de Negocio": r["unidad_negocio"] or "N/A",
            "Método de Pago": r["metodo_pago"] or "N/A",
            "Estado Orden": r["estado_orden"],
            "Estado OcAdmin": r["estado_ocadmin"] or "N/A",
            "Tipo Contracargo": r["tipo_contracargo"] or "N/A",
            "No. Autorización": r["autorizacion"],
            "Fecha Transacción": str(r["fecha_transaccion"]),
            "Fecha Contracargo": str(r["fecha_contracargo"]),
            "P&L": r["p_and_l"],
            "Fecha Docs Enviada": str(r["fecha_info_enviada"]) if r["fecha_info_enviada"] else "",
            "Fecha Resolución": str(r["fecha_resolucion"]) if r["fecha_resolucion"] else "",
            "Resolución": r["resolucion"],
            "Observaciones": r["observaciones"] or ""
        })
        
    df = pd.DataFrame(datos)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Contracargos')
    output.seek(0)
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Reporte_Contracargos.xlsx"}
    )

@app.get("/api/alertas/tarjetas-duplicadas")
def tarjetas_duplicadas():
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT tarjeta_mascarada, COUNT(id) as total, COALESCE(SUM(monto), 0) as monto_total
        FROM contracargos
        GROUP BY tarjeta_mascarada
        HAVING COUNT(id) > 1;
    """
    cursor.execute(query)
    resultados = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return [
        {
            "tarjeta_mascarada": r["tarjeta_mascarada"],
            "total_casos": r["total"],
            "monto_total": float(r["monto_total"])
        } for r in resultados
    ]

@app.get("/api/reportes/cuatro-semanas")
def reporte_cuatro_semanas():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Traer solo los registros existentes
        query = """
            SELECT 
                pasarela,
                estado_orden,
                TO_CHAR(fecha_contracargo, 'IYYY-IW') as semana_iso,
                COALESCE(SUM(monto), 0) as monto_total,
                COUNT(id) as cantidad
            FROM contracargos
            WHERE fecha_contracargo >= CURRENT_DATE - INTERVAL '35 days'
            GROUP BY pasarela, estado_orden, TO_CHAR(fecha_contracargo, 'IYYY-IW')
            ORDER BY semana_iso ASC;
        """
        cursor.execute(query)
        registros = cursor.fetchall()
        cursor.close()
        conn.close()

        # 2. Extraer ÚNICAMENTE las semanas que tienen registros reales
        semanas_info = sorted(list(set(r["semana_iso"] for r in registros if r["semana_iso"])))
        
        # Si la BD estuviera completamente vacía, mostramos la semana actual por defecto
        if not semanas_info:
            hoy = datetime.now()
            year, week_num, _ = hoy.isocalendar()
            semanas_info = [f"{year}-{week_num:02d}"]

        # 3. Armar la matriz basándonos en esas semanas reales
        matriz_resultado = {}
        estados_requeridos = ["Entregada", "No entregado"]

        for pasarela in ["BAC", "Neonet"]:
            filas = []
            for estado in estados_requeridos:
                monto_por_semana = {}
                cantidad_por_semana = {}
                
                for sem in semanas_info:
                    match = next((r for r in registros if r["pasarela"] == pasarela and str(r["estado_orden"]).strip().lower() == estado.lower() and r["semana_iso"] == sem), None)
                    
                    if match:
                        monto_por_semana[sem] = float(match["monto_total"])
                        cantidad_por_semana[sem] = int(match["cantidad"])
                    else:
                        monto_por_semana[sem] = 0.0
                        cantidad_por_semana[sem] = 0

                filas.append({
                    "estado_orden": estado,
                    "montos": monto_por_semana,
                    "cantidades": cantidad_por_semana
                })

            matriz_resultado[pasarela] = {
                "semanas": semanas_info,
                "filas": filas
            }

        return matriz_resultado

    except Exception as e:
        print(f"Error en reportes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


@app.get("/api/reportes/exportar-matriz-excel")
def exportar_matriz_excel():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT 
                pasarela,
                estado_orden,
                TO_CHAR(fecha_contracargo, 'IYYY-IW') as semana_iso,
                COALESCE(SUM(monto), 0) as monto_total,
                COUNT(id) as cantidad
            FROM contracargos
            WHERE fecha_contracargo >= CURRENT_DATE - INTERVAL '35 days'
            GROUP BY pasarela, estado_orden, TO_CHAR(fecha_contracargo, 'IYYY-IW')
            ORDER BY semana_iso ASC;
        """
        cursor.execute(query)
        registros = cursor.fetchall()
        cursor.close()
        conn.close()

        semanas_info = sorted(list(set(r["semana_iso"] for r in registros if r["semana_iso"])))
        if not semanas_info:
            hoy = datetime.now()
            year, week_num, _ = hoy.isocalendar()
            semanas_info = [f"{year}-{week_num:02d}"]

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            for pasarela in ["BAC", "Neonet"]:
                filas_excel = []
                estados = ["Entregada", "No entregado"]
                
                for estado in estados:
                    fila = {"Estado Orden": estado}
                    for sem in semanas_info:
                        match = next((r for r in registros if r["pasarela"] == pasarela and str(r["estado_orden"]).strip().lower() == estado.lower() and r["semana_iso"] == sem), None)
                        fila[f"Monto_{sem}"] = float(match["monto_total"]) if match else 0.0
                    
                    for sem in semanas_info:
                        match = next((r for r in registros if r["pasarela"] == pasarela and str(r["estado_orden"]).strip().lower() == estado.lower() and r["semana_iso"] == sem), None)
                        fila[f"Cant_{sem}"] = int(match["cantidad"]) if match else 0
                    
                    filas_excel.append(fila)
                
                df = pd.DataFrame(filas_excel)
                df.to_excel(writer, index=False, sheet_name=f"Matriz {pasarela}")

        output.seek(0)
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=Matriz_Contracargos_Unificada.xlsx"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error exportando Excel: {str(e)}")
    
@app.get("/api/reportes/semanal-matriz")
def reporte_matriz():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT pasarela, fecha_contracargo, estado_orden, monto, no_orden FROM contracargos;")
    registros = cursor.fetchall()
    cursor.close()
    conn.close()
    
    data = []
    for r in registros:
        semana_num = r["fecha_contracargo"].isocalendar()[1] if hasattr(r["fecha_contracargo"], 'isocalendar') else datetime.strptime(str(r["fecha_contracargo"]), "%Y-%m-%d").isocalendar()[1]
        data.append({
            "pasarela": r["pasarela"],
            "semana": f"Semana {semana_num}",
            "estado_orden": r["estado_orden"],
            "monto": float(r["monto"]),
            "orden": r["no_orden"]
        })
        
    df = pd.DataFrame(data)
    if df.empty:
        return []
    
    res = df.groupby(["pasarela", "semana", "estado_orden"]).agg(
        monto_total=("monto", "sum"),
        cantidad_ordenes=("orden", "count")
    ).reset_index()
    
    return res.to_dict(orient="records")

@app.get("/api/fraudes-preventivos")
def obtener_fraudes_preventivos():
    """
    Lee exclusivamente la pestaña 'Sheet1' de Google Sheets, realiza un cruce
    con la tabla local 'contracargos' por el número de orden y devuelve
    si ya recibieron contracargo y la fecha exacta registrada en la BD.
    """
    try:
        # 1. Conexión con Google Sheets
        gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_FILE)
        sheet = gc.open("Reporte de Ordenes con Fraude").worksheet("Sheet1")
        filas = sheet.get_all_values()

        if not filas:
            return []

        encabezados = filas[0]
        datos_filas = filas[1:]

        # 2. Consultar contracargos existentes en la base de datos local
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT no_orden, fecha_contracargo FROM contracargos;")
        
        # Mapeo: {'no_orden': 'fecha_contracargo'}
        mapa_contracargos = {}
        for row in cursor.fetchall():
            no_orden_str = str(row['no_orden']).strip()
            fecha_val = str(row['fecha_contracargo']) if row.get('fecha_contracargo') else "Registrado"
            mapa_contracargos[no_orden_str] = fecha_val

        cursor.close()
        conn.close()

        resultado = []
        for fila in datos_filas:
            row = {encabezados[i]: fila[i] for i in range(min(len(encabezados), len(fila)))}
            
            id_orden_drive = str(row.get("ID Orden", "")).strip()
            if not id_orden_drive:
                continue

            tiene_contracargo = id_orden_drive in mapa_contracargos
            fecha_match = mapa_contracargos.get(id_orden_drive, "-")

            resultado.append({
                "id_orden": id_orden_drive,
                "nombre_cliente": row.get("Nombre cliente", ""),
                "correo_cliente": row.get("Correo cliente", ""),
                "tarjeta": row.get("Tarjeta", ""),
                "total_orden": row.get("Total Orden", 0),
                "fecha_colocacion": row.get("Fecha Colocación", ""),
                "estatus_contracargo": "Contracargo Recibido" if tiene_contracargo else "Sin Contracargo",
                "fecha_contracargo_sistema": fecha_match
            })

        return resultado

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al sincronizar Google Sheets: {str(e)}")