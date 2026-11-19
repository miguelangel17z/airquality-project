# Preguntas de negocio

Las hipótesis orientan la analítica de los módulos posteriores. El análisis abarcará los 50 estados y el Distrito de Columbia, con resultados limitados a los sitios y condados con observaciones válidas en 2024. Las reglas de calidad y la definición de un día válido están en el [diccionario de datos](diccionario-datos.md). Los umbrales de cobertura propuestos son decisiones académicas para comparar observaciones. Se publicarán los tamaños de muestra, la cobertura y las exclusiones junto a cada resultado.

## P1. ¿Qué estados y condados tienen la mayor concentración promedio de ozono durante 2024?

- **Objetivo.** Ordenar los estados y condados monitoreados por concentración promedio de ozono en ppm.
- **Hipótesis inicial.** Algunos estados y condados presentarán medias superiores al resto debido a diferencias en la formación y el transporte del ozono. [EPA sobre el ozono troposférico](https://www.epa.gov/ozone-pollution-and-your-patients-health/what-ozone).
- **Datasets y campos.** `ozone_measurements` aporta `state_code`, `county_code`, `state_name`, `county_name`, `site_number`, `parameter_code`, `poc`, `date_local`, `time_local`, `timestamp_utc`, `sample_measurement`, `units_of_measure` y `qualifier`. `stations` es opcional para contexto geográfico o tipología mediante la clave del sitio, `latitude`, `longitude` y `location_setting`.
- **Agregaciones.** Promediar el ozono de los días válidos con igual peso por día para cada condado. Para cada estado, promediar las medias de sus condados elegibles con igual peso por condado. Publicar conteos de días por condado, sitios contribuyentes y número de condados contribuyentes por estado.
- **Visualización.** Dos gráficos de barras horizontales, uno por nivel geográfico, con cobertura junto a cada media.
- **Utilidad.** Identificar lugares prioritarios para revisar episodios, estacionalidad o cobertura de monitoreo.
- **Limitaciones.** El ranking principal exige al menos 180 días válidos para un condado y al menos 15 días válidos en cada uno de seis meses o más. Los estados entran con al menos un condado elegible y se identifica expresamente el caso de un solo condado. Las redes estacionales pueden quedar fuera. El resultado representa los sitios monitoreados y carece de ponderación por población o superficie.

## P2. ¿En qué meses se registran las concentraciones promedio más altas de ozono?

- **Objetivo.** Comparar los doce meses de 2024 controlando cambios en la composición geográfica de la muestra.
- **Hipótesis inicial.** Los meses más cálidos tenderán a presentar medias más altas por las condiciones favorables para la formación fotoquímica. [EPA sobre formación de ozono](https://www.epa.gov/ozone-pollution-and-your-patients-health/what-ozone).
- **Dataset y campos.** `ozone_measurements` aporta `state_code`, `county_code`, `site_number`, `parameter_code`, `poc`, `date_local`, `time_local`, `timestamp_utc`, `sample_measurement`, `units_of_measure` y `qualifier`. Derivar el mes de `date_local`.
- **Agregaciones.** Promediar los días válidos dentro de cada condado y mes. Promediar las medias mensuales con igual peso por condado. Informar conteos de condados, sitios y días, junto con la dispersión entre condados.
- **Visualización.** Serie de doce puntos con banda intercuartílica entre condados y barras de cobertura.
- **Utilidad.** Identificar períodos para priorizar seguimiento y comunicación sobre ozono.
- **Limitaciones.** El panel principal conserva los mismos condados en los doce meses, con al menos el 75 % de los días del calendario válidos en cada mes. Mostrar como sensibilidad la serie de condados elegibles en cada mes, con su cobertura. Si el panel común queda vacío, se informa y se presenta la serie de sensibilidad como exploratoria. Los patrones corresponden a un solo año.

## P3. ¿Qué diferencias existen en la concentración promedio de ozono entre días laborales y fines de semana?

- **Objetivo.** Cuantificar la diferencia descriptiva entre lunes a viernes y sábado a domingo dentro de los mismos condados y meses.
- **Hipótesis inicial.** Habrá diferencias en parte de los condados, con magnitud y dirección variables.
- **Dataset y campos.** `ozone_measurements` aporta `state_code`, `county_code`, `site_number`, `parameter_code`, `poc`, `date_local`, `time_local`, `timestamp_utc`, `sample_measurement`, `units_of_measure` y `qualifier`. Derivar el mes y el día de la semana de `date_local`.
- **Agregaciones.** Para cada condado y mes, calcular las dos medias de ozono diario y su diferencia en ppm como fin de semana menos lunes a viernes. Resumir con igual peso por par condado-mes e informar conteos de días por grupo, estratos válidos y dispersión de las diferencias.
- **Visualización.** Gráfico de puntos o cajas de las diferencias con referencia en cero y filtro por mes o estado.
- **Utilidad.** Identificar perfiles semanales para orientar estudios posteriores sobre condiciones ambientales o actividades humanas.
- **Limitaciones.** Exigir al menos cuatro días válidos de cada grupo por condado y mes. El calendario usa el día de la semana, de modo que los festivos entre semana conservan su grupo. La meteorología, los cambios de sitios y el horario de monitoreo pueden influir en las diferencias. Los tres datasets carecen de datos sobre tráfico y emisiones que permitan explicar la causa.

## P4. ¿En qué horarios del día se presentan las mayores concentraciones promedio de ozono?

- **Objetivo.** Estimar el perfil de concentración por hora estándar local y detectar las horas con mayor media.
- **Hipótesis inicial.** El perfil agregado registrará medias más altas en horas de la tarde, con variaciones entre regiones y meses. [EPA sobre patrones horarios del ozono](https://www.epa.gov/ozone-pollution-and-your-patients-health/what-ozone).
- **Dataset y campos.** `ozone_measurements` aporta `state_code`, `county_code`, `site_number`, `parameter_code`, `poc`, `date_local`, `time_local`, `timestamp_utc`, `sample_measurement`, `units_of_measure` y `qualifier`.
- **Agregaciones.** Usar el conjunto común de estaciones-día con 24 horas válidas para comparar las 24 franjas. Para cada hora y condado-día, promediar los sitios elegibles con igual peso. Promediar los días dentro de cada condado y después los condados con igual peso. Publicar días, sitios y condados contribuyentes.
- **Visualización.** Curva horaria y mapa de calor de hora local por mes con filtros geográficos.
- **Utilidad.** Identificar franjas prioritarias para investigar el comportamiento diurno y comunicarlo.
- **Limitaciones.** El panel completo puede favorecer estaciones con monitoreo continuo. Mostrar una sensibilidad con días de al menos 18 horas y detallar la cobertura por franja. La hora estándar local permanece fija durante el año y puede diferir de la hora civil de verano.

## P5. ¿Qué condados presentan el mayor porcentaje de días con un AQI superior a 100?

- **Objetivo.** Ordenar condados por la frecuencia de valores diarios de AQI estrictamente mayores que 100.
- **Hipótesis inicial.** La proporción variará entre condados monitoreados y el contaminante que define el AQI variará entre días.
- **Dataset y campos.** `daily_air_quality` aporta `state_code`, `county_code`, `state_name`, `county_name`, `aqi_date`, `aqi` y, como contexto, `defining_parameter` y `number_of_sites_reporting`.
- **Agregaciones.** Calcular `100 × días válidos con AQI > 100 ÷ días con AQI válido del condado`. Contar fechas distintas después de validar la clave. Publicar el numerador, el denominador y la cobertura frente a los 366 días de 2024. Un condado con cero días válidos tiene una proporción indefinida.
- **Visualización.** Barras ordenadas con etiqueta de días válidos y desglose opcional del contaminante dominante en días sobre el umbral.
- **Utilidad.** Identificar condados para revisar episodios recurrentes de mala calidad del aire.
- **Limitaciones.** Exigir al menos 180 días válidos para el ranking principal y mostrar los demás condados como cobertura insuficiente. Una fecha sin AQI válido queda fuera del denominador y la ausencia puede ser estacional. El AQI refleja los contaminantes reportados que compiten por definir el índice del día. [EPA sobre el AQI](https://www.epa.gov/outdoor-air-quality-data/about-airdata-reports).

## P6. ¿Qué relación estadística descriptiva existe entre la concentración diaria promedio de ozono y el AQI de cada condado?

- **Objetivo.** Estimar por condado la asociación de la media diaria de ozono (ppm) con el AQI diario publicado.
- **Hipótesis inicial.** La asociación tenderá a ser positiva, con diferencias entre días en que el AQI lo define el ozono y días en que lo define otro contaminante.
- **Datasets y campos.** `ozone_measurements` aporta `state_code`, `county_code`, `site_number`, `parameter_code`, `poc`, `date_local`, `time_local`, `timestamp_utc`, `sample_measurement`, `units_of_measure` y `qualifier`. `daily_air_quality` aporta `state_code`, `county_code`, `aqi_date`, `aqi` y `defining_parameter`.
- **Agregaciones.** Agregar primero a un par único de ozono medio y fecha local por condado. Verificar la clave de ambas series y cruzar `state_code`, `county_code` y fecha con uniones uno a uno. Calcular Pearson y Spearman por condado. Informar el número de pares, los días sin correspondencia en cada serie y, si hay cobertura, repetir por estrato de contaminante definitorio.
- **Visualización.** Dispersión por condado con un punto por día emparejado, color por contaminante que define el AQI y anotación de correlaciones y número de pares.
- **Utilidad.** Determinar dónde las tendencias del ozono se parecen a las del índice general y orientar revisiones por contaminante.
- **Limitaciones.** Exigir al menos 30 pares válidos y variación en ambas series, también al reportar un estrato. La media de medidas horarias y el máximo publicado del AQI describen fenómenos distintos y usan ventanas de agregación diferentes. El monitoreo desigual, los datos faltantes, la dependencia temporal y la estacionalidad limitan la interpretación. La correlación describe asociación. Atribuir causas requeriría un diseño y variables adicionales. [EPA sobre el AQI por condado](https://aqs.epa.gov/aqsweb/documents/aqi/aqi_data.html).
