from flask import Flask, jsonify
import pandas as pd
from datetime import datetime, timedelta

from flask_cors import CORS

app = Flask(__name__)

CORS(app)

def dividir_en_slots(inicio, fin, duracion=40):
    slots = []
    actual = inicio
    while actual + timedelta(minutes=duracion) <= fin:
        slots.append((actual, actual + timedelta(minutes=duracion)))
        actual += timedelta(minutes=duracion)
    return slots

@app.route("/asignaciones", methods=["GET"])
def asignaciones():
    # 1. Leer CSVs
    estudiantes_df = pd.read_csv("estudiantes.csv")
    profesores_df = pd.read_csv("horario_profesores.csv")
    universidad_df = pd.read_csv("horario_universidad.csv")

    # 2. Preparar datos
    dias_semana = {
        'Lunes': 1, 'Martes': 2, 'Miércoles': 3,
        'Jueves': 4, 'Viernes': 5, 'Sábado': 6, 'Domingo': 7
    }
    universidad_df['DiaTexto'] = universidad_df['Dia']
    universidad_df['Dia'] = universidad_df['Dia'].map(dias_semana)

    def convertir_dt(row, hora_col):
        return datetime.strptime(f"{row['Año']}-{row['Mes']:02d}-{row['Dia']:02d} {row[hora_col]}", "%Y-%m-%d %H:%M")

    profesores_df['InicioDT'] = profesores_df.apply(lambda row: convertir_dt(row, 'HoraInicio'), axis=1)
    profesores_df['FinDT'] = profesores_df.apply(lambda row: convertir_dt(row, 'HoraFin'), axis=1)
    universidad_df['InicioDT'] = universidad_df.apply(lambda row: convertir_dt(row, 'HoraInicio'), axis=1)
    universidad_df['FinDT'] = universidad_df.apply(lambda row: convertir_dt(row, 'HoraFin'), axis=1)

    estudiantes_df = estudiantes_df.rename(columns={"Dia": "day", "Mes": "month", "Año": "year"})
    estudiantes_df["Fecha"] = pd.to_datetime(estudiantes_df[["year", "month", "day"]])

    # 3. Generar slots
    slots_aulas = []
    for _, row in universidad_df.iterrows():
        for inicio, fin in dividir_en_slots(row['InicioDT'], row['FinDT']):
            slots_aulas.append({
                'Sala': row['Sala'],
                'Inicio': inicio,
                'Fin': fin,
                'Fecha': inicio.date()
            })

    slots_profesores = []
    for _, row in profesores_df.iterrows():
        for inicio, fin in dividir_en_slots(row['InicioDT'], row['FinDT']):
            slots_profesores.append({
                'ProfesorID': row['ProfesorID'],
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
                        "EstudianteID": est["EstudianteID"],
                        "TituloTesis": est["TesisTitulo"],
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

    return jsonify(eventos)

if __name__ == "__main__":
    app.run(debug=True)
