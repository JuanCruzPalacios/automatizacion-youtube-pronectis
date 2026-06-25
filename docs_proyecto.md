# Historia del Proyecto: Automatización YouTube Pronectis

## 1. El Requerimiento Inicial y Contexto

El proyecto nace a partir de una necesidad interna de optimización de tiempo y recursos para la gestión de contenido multimedia. El requerimiento original planteado por la dirección establecía el siguiente objetivo base en un plazo operativo de dos semanas:

> *Tenemos que armar una automatización que haga lo siguiente:*
> *Pasamos una URL de un video de YouTube, se debe descargar, agregar comienzo y fin de equipo.*
> *Subir el nuevo video a nuestro canal con una descripción del mismo y una referencia alusiva a la landing web correspondiente.*
> *Con los recursos de Google solamente creo que no alcanza, tal vez debamos sumar Make al tema...*

El objetivo principal era eliminar el trabajo manual repetitivo involucrado en la curación, empaquetado corporativo (branding con intros y outros) y publicación de los videos.

### El Enfoque de Desarrollo: Python Nativo vs. Make

Ante la sugerencia inicial de emplear herramientas *No-Code* o *Low-Code* como Make (Integromat), se realizó un análisis de factibilidad técnica. La decisión arquitectónica fue **descartar Make** e implementar una solución 100% nativa en **Python**.

**Justificación Tecnológica:**
- **Costos Operativos:** Las plataformas de automatización cobran por "operaciones" o consumo de datos. El procesamiento de video de alta calidad (descarga, renderizado y subida) habría saturado las cuotas de datos rápidamente, generando un gasto mensual insostenible.
- **Control y Flexibilidad:** Un desarrollo nativo permite invocar directamente motores de renderizado como `FFmpeg` y realizar procesamiento asíncrono sin los límites de tiempo de ejecución estrictos (timeouts) típicos de las plataformas No-Code.
- **Independencia Tecnológica:** La empresa mantiene la propiedad absoluta del código sin depender de cambios en las políticas de precios de terceros.

---

## 2. Evolución del Sistema y Fases de Implementación

El proyecto se estructuró de forma iterativa, permitiendo tener un producto mínimo viable rápidamente y luego escalar su complejidad.

### Fase 1: Implementación Inicial (V1)
La primera iteración logró exitosamente cumplir con los requisitos básicos solicitados en el requerimiento inicial.
- **Web UI Básica:** Se desarrolló una interfaz gráfica con un Panel de Revisión que permitía supervisar el contenido antes de su publicación en YouTube (Human-in-the-Loop).
- **Procesamiento de Video Estándar:** Capacidades exclusivas para videos en formato horizontal (16:9), empalmando automáticamente las cortinillas corporativas (intro/outro).
- **Inteligencia Artificial Temprana:** Integración fundamental para automatizar la redacción de un título y una descripción coherentes según el contenido del video.

### Fase 2: Madurez Arquitectónica y Escalabilidad (V2 - Versión Actual)
La Fase 2 transformó la herramienta de un automatizador simple a un motor integral de creación y gestión de contenido potenciado por los últimos avances en Inteligencia Artificial y Cloud Computing.

**Nuevas Capacidades Incorporadas:**
- **Soporte para Formato Vertical (Shorts):** Lógica avanzada para detectar, redimensionar y empaquetar videos verticales con fondos difuminados, adaptando el renderizado a la resolución móvil sin deformar la imagen.
- **Sistema de Proyectos Persistentes:** Se introdujo la capacidad de almacenar los últimos proyectos procesados, permitiendo retomar flujos de trabajo pausados o cancelados.
- **Módulo de Contexto para IA:** Capacidad para que el usuario inyecte instrucciones específicas y directrices, dándole contexto al modelo antes de que redacte.
- **Generación Multimodal y Miniaturas:** Creación automática de portadas de YouTube en alta resolución con posicionamiento automático del logo corporativo, impulsado por IA.
- **Edición en Caliente:** Nueva función en el panel web para solicitarle a la IA que reescriba individualmente cualquier campo (título, descripción, tags, o miniatura) con el que el usuario no esté satisfecho, pidiéndole un contexto o instrucción adicional en tiempo real para afinar el resultado.
- **Clasificación Avanzada:** Etiquetado dinámico (Tags) y selección manual y precisa de Listas de Reproducción (Playlists) desde la interfaz antes de subir el video.

---

## 3. Desafíos Técnicos Centrales

Durante el desarrollo de la Fase 2 hacia el despliegue en producción en la nube, se superaron múltiples barreras técnicas de alta complejidad:

1. **Gestión de Recursos Cloud (Ahorro Operativo y Encendido On-Demand):**
   - **El Desafío:** Mantener un servidor con capacidad para renderizar video pesado prendido 24/7 generaba un desperdicio enorme de recursos económicos en los momentos de inactividad.
   - **La Solución:** Se diseñó una infraestructura de doble capa en Google Cloud. Un frontal hiper-liviano (Cloud Run) que permanece siempre activo a un costo irrisorio, cuya única función es actuar de portal. Cuando el usuario lo requiere, este portal envía una señal que **enciende On-Demand** a la Máquina Virtual principal (VM). A su vez, la VM cuenta con un sofisticado monitor interno por WebSockets que detecta inactividad (15 minutos) y, si no hay procesos de renderizado corriendo, **apaga el servidor automáticamente**, garantizando un ahorro masivo en la facturación de infraestructura.

2. **Interferencia de Redes con la Lógica de Inactividad:**
   - **El Desafío:** Los Load Balancers (balanceadores de carga) de Google Cloud envían "Pings" de comprobación (Health Checks) al servidor cada 10 segundos. El script de auto-apagado interpretaba estos pings automatizados como "tráfico de usuarios activos", impidiendo que la máquina se apagara.
   - **La Solución:** Se programó un Middleware (filtro HTTP) en el código principal capaz de distinguir a un navegador web real de los "Health Checks" de Google Cloud, ignorando estos últimos para que el temporizador de inactividad fluyera correctamente.

3. **Integración con IA de Última Generación y Permisos GCP:**
   - **El Desafío:** La implementación inicial para generar miniaturas presentaba bloqueos por restricciones de acceso a modelos Enterprise en cuentas regulares.
   - **La Solución:** Se migró exitosamente toda la lógica de prompting visual para operar nativamente bajo **Gemini 3.1 Flash-Image**, esquivando "alucinaciones" visuales mediante *prompt engineering* riguroso.

4. **Integración Segura con la API de YouTube (OAuth 2.0):**
   - **El Desafío:** Publicar contenido corporativo programáticamente exige cumplir con las estrictas Políticas de Seguridad de Google.
   - **La Solución:** Se implementó un flujo de autenticación robusto mediante OAuth 2.0, el cual genera y renueva un `youtube_token.pickle` de forma "eterna" sin intervención manual. Esto asegura que el sistema pueda operar indefinidamente sin expiración de sesiones, automatizando por completo la subida de videos, asignación a playlists y carga de miniaturas personalizadas.

---

## Conclusión y Beneficios Corporativos

El proyecto logró entregar una herramienta que excede ampliamente el requerimiento inicial, proporcionando a Pronectis un control absoluto e in-house sobre su pipeline de marketing audiovisual.

**Impacto en el Negocio (Resultados Directos):**
- **Ahorro de Infraestructura:** El modelo On-Demand evita el pago de servidores 24/7, garantizando que solo se facture el tiempo exacto de renderizado.
- **Reducción del Trabajo Manual:** La inyección automática de miniaturas, redacción SEO con IA y empaquetado de video horizontal/vertical (Shorts) liberó horas de trabajo operativo diario, delegando la responsabilidad humana únicamente al clic final en el "Panel de Revisión".
