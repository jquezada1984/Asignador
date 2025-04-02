from flask import Flask, jsonify
from flask_cors import CORS
import pandas as pd
import pymysql
from datetime import datetime, timedelta

from flask_cors import CORS

app = Flask(__name__)

CORS(app)

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'ube_titulacion'
}


def dividir_en_slots(inicio, fin, duracion=40):
    slots = []
    actual = inicio
    while actual + timedelta(minutes=duracion) <= fin:
        slots.append((actual, actual + timedelta(minutes=duracion)))
        actual += timedelta(minutes=duracion)
    return slots

@app.route("/asignaciones", methods=["GET"])
def asignaciones():
    conn = pymysql.connect(**db_config)

    # 1. Leer CSVs
     # 1. Leer desde la base de datos
    estudiantes_df = pd.read_sql_query("SELECT * FROM disponibilidad_defensa_tesis where estado=1", conn)
    profesores_df = pd.read_sql_query("SELECT * FROM horarios_tribunales where estado=1", conn)
    universidad_df = pd.read_sql_query("SELECT * FROM horario_sala_disponible where estado=1", conn)


    # 2. Preparar datos
    dias_semana = {
        'Lunes': 1, 'Martes': 2, 'Miércoles': 3,
        'Jueves': 4, 'Viernes': 5, 'Sábado': 6, 'Domingo': 7
    }
    universidad_df['DiaTexto'] = universidad_df['dia_semana']
    universidad_df['Dia'] = universidad_df['dia_semana'].map(dias_semana)

    def convertir_dt(row, hora_col):
        hora_str = str(row[hora_col])
        if "day" in hora_str:  # por si viene como timedelta
            hora_str = hora_str.split(" ")[-1]
        return datetime.strptime(f"{row['anio']}-{row['mes']:02d}-{row['dia']:02d} {hora_str}", "%Y-%m-%d %H:%M:%S")

    profesores_df['InicioDT'] = profesores_df.apply(lambda row: convertir_dt(row, 'hora_inicio'), axis=1)
    profesores_df['FinDT'] = profesores_df.apply(lambda row: convertir_dt(row, 'hora_fin'), axis=1)

    universidad_df['InicioDT'] = universidad_df.apply(lambda row: convertir_dt(row, 'hora_inicio'), axis=1)
    universidad_df['FinDT'] = universidad_df.apply(lambda row: convertir_dt(row, 'hora_fin'), axis=1)

    estudiantes_df = estudiantes_df.rename(columns={"dia": "day", "mes": "month", "anio": "year"})
    estudiantes_df["Fecha"] = pd.to_datetime(estudiantes_df[["year", "month", "day"]])

    # 3. Generar slots
    slots_aulas = []
    for _, row in universidad_df.iterrows():
        for inicio, fin in dividir_en_slots(row['InicioDT'], row['FinDT']):
            slots_aulas.append({
                'Sala': row['id_sala'],
                'Inicio': inicio,
                'Fin': fin,
                'Fecha': inicio.date()
            })

    slots_profesores = []
    for _, row in profesores_df.iterrows():
        for inicio, fin in dividir_en_slots(row['InicioDT'], row['FinDT']):
            slots_profesores.append({
                'ProfesorID': row['id_tribunal'],
                'Inicio': inicio,
                'Fin': fin,
                'Fecha': inicio.date()
            })

    df_slots_aulas = pd.DataFrame(slots_aulas)
    df_slots_profesores = pd.DataFrame(slots_profesores)

    # 4. Generar combinaciones válidas
    combinaciones = []
    for _, est in estudiantes_df.iterrows():
        fecha_est = est["Fecha"].date()
        for _, slot_alu in df_slots_aulas.iterrows():
            if slot_alu["Fecha"] != fecha_est:
                continue
            for _, slot_pro in df_slots_profesores.iterrows():
                if (slot_pro["Fecha"] == fecha_est and
                    slot_pro["Inicio"] == slot_alu["Inicio"] and
                    slot_pro["Fin"] == slot_alu["Fin"]):
                    combinaciones.append({
                        "EstudianteID": est["id_disponibilidad"],
                        "TituloTesis": est["titulo"],
                        "HoraInicio": slot_alu["Inicio"],
                        "HoraFin": slot_alu["Fin"],
                        "ProfesorID": slot_pro["ProfesorID"],
                        "Sala": slot_alu["Sala"]
                    })

    df_comb = pd.DataFrame(combinaciones).sort_values(by="HoraInicio")

    # 5. Aplicar algoritmo greedy con reutilización de profesores y aulas en diferentes horarios
    eventos = []
    usados_estudiantes = set()
    slots_ocupados_profesor = {}
    slots_ocupados_aula = {}
    id_evento = 1

    for _, row in df_comb.iterrows():
        estudiante_id = row["EstudianteID"]
        profesor_id = row["ProfesorID"]
        sala = row["Sala"]
        hora_inicio = row["HoraInicio"]
        hora_fin = row["HoraFin"]

        if estudiante_id in usados_estudiantes:
            continue

        if hora_inicio in slots_ocupados_profesor.get(profesor_id, set()):
            continue

        if hora_inicio in slots_ocupados_aula.get(sala, set()):
            continue

        eventos.append({
            "id": id_evento,
            "title": row["TituloTesis"],
            "start": hora_inicio.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": hora_fin.strftime("%Y-%m-%dT%H:%M:%S"),
            "extendedProps": {
                "calendar": "Success"
            }
        })

        id_evento += 1
        usados_estudiantes.add(estudiante_id)
        slots_ocupados_profesor.setdefault(profesor_id, set()).add(hora_inicio)
        slots_ocupados_aula.setdefault(sala, set()).add(hora_inicio)

     

    # Guardar en la base de datos
    for ev in eventos:
        estudiante_id = next((c["EstudianteID"] for c in combinaciones if c["TituloTesis"] == ev["title"] and c["HoraInicio"] == datetime.strptime(ev["start"], "%Y-%m-%dT%H:%M:%S")), None)
        profesor_id = next((c["ProfesorID"] for c in combinaciones if c["TituloTesis"] == ev["title"] and c["HoraInicio"] == datetime.strptime(ev["start"], "%Y-%m-%dT%H:%M:%S")), None)
        sala = next((c["Sala"] for c in combinaciones if c["TituloTesis"] == ev["title"] and c["HoraInicio"] == datetime.strptime(ev["start"], "%Y-%m-%dT%H:%M:%S")), None)

        with conn.cursor() as cursor:
            cursor.execute("UPDATE asignaciones_eventos SET estado = 2 WHERE estudiante_id=%s", (estudiante_id,))

        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO asignaciones_eventos (estudiante_id, titulo_tesis, hora_inicio, hora_fin, profesor_id, sala)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (estudiante_id, ev["title"], ev["start"], ev["end"], profesor_id, sala))
    conn.commit()

    return jsonify(eventos)


@app.route("/asignaciones/guardadas", methods=["GET"])
def asignaciones_guardadas():
    conn = pymysql.connect(**db_config)
    query = """
        SELECT id, estudiante_id, titulo_tesis, hora_inicio, hora_fin, profesor_id, sala,estado
        FROM asignaciones_eventos
    """
    df = pd.read_sql_query(query, conn)

    eventos = []
    for _, row in df.iterrows():
        eventos.append({
            "id": row["id"],
            "title": row["titulo_tesis"],
            "start": row["hora_inicio"].strftime("%Y-%m-%dT%H:%M:%S"),
            "end": row["hora_fin"].strftime("%Y-%m-%dT%H:%M:%S"),
            "extendedProps": {
                "profesorId": row["profesor_id"],
                "sala": row["sala"],
                "estudianteId": row["estudiante_id"],
                "calendar": "Success" if row["estado"] == 1 else "Danger"
            }
        })

    return jsonify(eventos)



if __name__ == "__main__":
    app.run(debug=True)

