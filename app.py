from flask import Flask, jsonify
from flask_cors import CORS
import pandas as pd
import pymysql
from datetime import datetime, timedelta
from collections import defaultdict



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
    
    # 1. Consultas
    estudiantes_df = pd.read_sql_query("SELECT * FROM disponibilidad_defensa_tesis WHERE estado=1", conn)
    profesores_df = pd.read_sql_query("SELECT * FROM horarios_tribunales WHERE estado=1", conn)
    universidad_df = pd.read_sql_query("SELECT * FROM horario_sala_disponible WHERE estado=1", conn)

    # 2. Preparar fechas
    dias_semana = {
        'Lunes': 1, 'Martes': 2, 'Miércoles': 3,
        'Jueves': 4, 'Viernes': 5, 'Sábado': 6, 'Domingo': 7
    }

    universidad_df['Dia'] = universidad_df['dia_semana'].map(dias_semana)

    def convertir_dt(row, hora_col):
        hora_str = str(row[hora_col])
        if "day" in hora_str:
            hora_str = hora_str.split(" ")[-1]
        return datetime.strptime(f"{row['anio']}-{row['mes']:02d}-{row['dia']:02d} {hora_str}", "%Y-%m-%d %H:%M:%S")

    profesores_df['InicioDT'] = profesores_df.apply(lambda row: convertir_dt(row, 'hora_inicio'), axis=1)
    profesores_df['FinDT'] = profesores_df.apply(lambda row: convertir_dt(row, 'hora_fin'), axis=1)
    universidad_df['InicioDT'] = universidad_df.apply(lambda row: convertir_dt(row, 'hora_inicio'), axis=1)
    universidad_df['FinDT'] = universidad_df.apply(lambda row: convertir_dt(row, 'hora_fin'), axis=1)
    estudiantes_df = estudiantes_df.rename(columns={"dia": "day", "mes": "month", "anio": "year"})
    estudiantes_df["Fecha"] = pd.to_datetime(estudiantes_df[["year", "month", "day"]])

    # 3. Slots por sala y profesor
    def dividir_en_slots(inicio, fin, duracion_min=60):
        slots = []
        actual = inicio
        while actual + timedelta(minutes=duracion_min) <= fin:
            slots.append((actual, actual + timedelta(minutes=duracion_min)))
            actual += timedelta(minutes=duracion_min)
        return slots

    slots_aulas = []
    for _, row in universidad_df.iterrows():
        for inicio, fin in dividir_en_slots(row['InicioDT'], row['FinDT']):
            slots_aulas.append({'Sala': row['id_sala'], 'Inicio': inicio, 'Fin': fin, 'Fecha': inicio.date()})

    slots_profesores = []
    for _, row in profesores_df.iterrows():
        for inicio, fin in dividir_en_slots(row['InicioDT'], row['FinDT']):
            slots_profesores.append({'ProfesorID': row['id_tribunal'], 'Inicio': inicio, 'Fin': fin, 'Fecha': inicio.date()})

    df_slots_aulas = pd.DataFrame(slots_aulas)
    df_slots_profesores = pd.DataFrame(slots_profesores)

    # 4. Asignación greedy
    profesores_carga = defaultdict(int)
    eventos = []
    salas_ocupadas = defaultdict(set)
    profesores_ocupados = defaultdict(set)
    estudiantes_asignados = set()
    id_evento = 1

    profesores_disponibles = profesores_df['id_tribunal'].unique().tolist()
    profesores_rotados = profesores_disponibles.copy()

    for _, estudiante in estudiantes_df.iterrows():
        fecha = estudiante["Fecha"].date()
        titulo = estudiante["titulo"]
        asignado = False

        for slot_alu in df_slots_aulas[df_slots_aulas["Fecha"] == fecha].itertuples():
            if slot_alu.Inicio in salas_ocupadas[slot_alu.Sala]:
                continue

            for profesor_id in profesores_rotados:
                slot_pro = df_slots_profesores[
                    (df_slots_profesores["ProfesorID"] == profesor_id) &
                    (df_slots_profesores["Fecha"] == fecha) &
                    (df_slots_profesores["Inicio"] == slot_alu.Inicio)
                ]

                if slot_pro.empty or slot_alu.Inicio in profesores_ocupados[profesor_id]:
                    continue

                eventos.append({
                    "id": id_evento,
                    "title": estudiante["titulo"],
                    "start": slot_alu.Inicio.strftime("%Y-%m-%dT%H:%M:%S"),
                    "end": slot_alu.Fin.strftime("%Y-%m-%dT%H:%M:%S"),
                    "extendedProps": {
                        "calendar": "Success"
                    },
                    "idProfesor": profesor_id
                })

                id_evento += 1
                estudiantes_asignados.add(estudiante["id_disponibilidad"])
                profesores_carga[profesor_id] += 1
                salas_ocupadas[slot_alu.Sala].add(slot_alu.Inicio)
                profesores_ocupados[profesor_id].add(slot_alu.Inicio)
                asignado = True
                break

            if asignado:
                break

        profesores_rotados = sorted(profesores_disponibles, key=lambda p: profesores_carga[p])

    # 5. Guardar resultados
    for ev in eventos:
        estudiante_id = next((c["id_disponibilidad"] for _, c in estudiantes_df.iterrows()
                             if c["titulo"] == ev["title"]), None)
        profesor_id = ev["idProfesor"]
        sala = next((c["Sala"] for c in slots_aulas if c["Inicio"] == datetime.strptime(ev["start"], "%Y-%m-%dT%H:%M:%S")), None)

        with conn.cursor() as cursor:
            cursor.execute("UPDATE asignaciones_eventos SET estado = 2 WHERE estudiante_id=%s", (estudiante_id,))
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
            "idProfesor":row["profesor_id"],
            "extendedProps": {
                "profesorId": row["profesor_id"],
                "sala": row["sala"],
                "estudianteId": row["estudiante_id"],
                "calendar": "Success" if row["estado"] == 1 else "Danger"
            },
        })

    return jsonify(eventos)



if __name__ == "__main__":
    app.run(debug=True)

