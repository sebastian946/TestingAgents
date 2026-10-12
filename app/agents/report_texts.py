"""Every text a person reads in the Excel report, per language (see config/languages.py).

The report code only looks texts up here, so adding a language means adding one entry.
Machine values (priority `critical`, category `security`, page type `login`) are stored in
English; the `values` maps translate them for display only.
"""

REPORT_TEXTS: dict[str, dict] = {
    "en": {
        "sheet_summary": "Summary",
        "sheet_scenarios": "Scenarios",
        "title": "Test scenario report",
        "subtitle": "Manual test scenarios designed for the pages explored on the site below.",
        "facts": {
            "site": "Site",
            "description": "App description",
            "generated": "Report generated (UTC)",
            "pages": "Pages explored",
            "scenarios": "Test scenarios",
            "uncovered": "Pages without scenarios",
        },
        "not_provided": "(not provided)",
        "by_priority": "Scenarios by priority",
        "by_category": "Scenarios by category",
        "priority": "Priority",
        "category": "Category",
        "meaning": "What it means",
        "count": "Scenarios",
        "total": "Total",
        "pages_section": "Pages explored",
        "pages_headers": ["Page", "Title", "Page type", "Scenarios"],
        "uncovered_note": "Pages with 0 scenarios could not be covered automatically; review them by hand.",
        "how_to": "How to use this report",
        "tips": [
            "1. Open the Scenarios sheet. Each row is one test a person can run in a browser.",
            "2. Use the filter arrows in the header to show one page, priority or category.",
            "3. Run the critical and high scenarios first; they cover the flows that matter most.",
            "4. Follow the steps in order and compare what you see with the expected result.",
            "5. Record the outcome in the Result column (Pass, Fail, Blocked) and add notes.",
            "Values in <angle brackets> are placeholders: replace them with real test data.",
        ],
        "columns": ["ID", "Page", "Page type", "Title", "Steps", "Expected result", "Priority", "Category", "Result", "Notes"],
        "result_options": ["Not run", "Pass", "Fail", "Blocked"],
        "result_prompt": "Pass, Fail, Blocked or Not run",
        "result_title": "Result",
        "doc_title": "Test scenario report",
        "file_prefix": "test-report",
        "priority_meaning": {
            "critical": "A core flow is blocked or there is a security or data risk. Run these first.",
            "high": "Important negative paths and validations on core flows.",
            "medium": "Secondary flows and validations on non-core forms.",
            "low": "Cosmetic or minor usability issues.",
        },
        "category_meaning": {
            "functional": "The main path does what it promises.",
            "negative": "Wrong input or misuse is handled correctly.",
            "validation": "Field rules: required, format, length, allowed values.",
            "security": "Authentication, access and data exposure.",
            "usability": "Clarity of messages, labels and flow.",
            "accessibility": "Labels, keyboard use and focus.",
        },
        "values": {},  # English shows the codes as they are
    },
    "es": {
        "sheet_summary": "Resumen",
        "sheet_scenarios": "Escenarios",
        "title": "Reporte de escenarios de prueba",
        "subtitle": "Escenarios de prueba manual diseñados para las páginas exploradas del sitio indicado abajo.",
        "facts": {
            "site": "Sitio",
            "description": "Descripción de la app",
            "generated": "Reporte generado (UTC)",
            "pages": "Páginas exploradas",
            "scenarios": "Escenarios de prueba",
            "uncovered": "Páginas sin escenarios",
        },
        "not_provided": "(no indicada)",
        "by_priority": "Escenarios por prioridad",
        "by_category": "Escenarios por categoría",
        "priority": "Prioridad",
        "category": "Categoría",
        "meaning": "Qué significa",
        "count": "Escenarios",
        "total": "Total",
        "pages_section": "Páginas exploradas",
        "pages_headers": ["Página", "Título", "Tipo de página", "Escenarios"],
        "uncovered_note": "Las páginas con 0 escenarios no se pudieron cubrir automáticamente; revísalas a mano.",
        "how_to": "Cómo usar este reporte",
        "tips": [
            "1. Abre la hoja Escenarios. Cada fila es una prueba que una persona puede ejecutar en el navegador.",
            "2. Usa las flechas de filtro del encabezado para ver una página, prioridad o categoría.",
            "3. Ejecuta primero los escenarios críticos y altos: cubren los flujos más importantes.",
            "4. Sigue los pasos en orden y compara lo que ves con el resultado esperado.",
            "5. Anota el resultado en la columna Resultado (Pasó, Falló, Bloqueado) y añade notas.",
            "Los valores entre <signos angulares> son marcadores: reemplázalos por datos de prueba reales.",
        ],
        "columns": ["ID", "Página", "Tipo de página", "Título", "Pasos", "Resultado esperado", "Prioridad", "Categoría", "Resultado", "Notas"],
        "result_options": ["Sin ejecutar", "Pasó", "Falló", "Bloqueado"],
        "result_prompt": "Pasó, Falló, Bloqueado o Sin ejecutar",
        "result_title": "Resultado",
        "doc_title": "Reporte de escenarios de prueba",
        "file_prefix": "reporte-pruebas",
        "priority_meaning": {
            "critical": "Un flujo principal queda bloqueado o hay un riesgo de seguridad o de datos. Ejecútalos primero.",
            "high": "Caminos negativos y validaciones importantes en los flujos principales.",
            "medium": "Flujos secundarios y validaciones en formularios no principales.",
            "low": "Problemas estéticos o de usabilidad menores.",
        },
        "category_meaning": {
            "functional": "El camino principal hace lo que promete.",
            "negative": "Las entradas incorrectas o el mal uso se manejan bien.",
            "validation": "Reglas de los campos: obligatorio, formato, longitud, valores permitidos.",
            "security": "Autenticación, acceso y exposición de datos.",
            "usability": "Claridad de mensajes, etiquetas y flujo.",
            "accessibility": "Etiquetas, uso con teclado y foco.",
        },
        "values": {
            # priorities
            "critical": "crítica", "high": "alta", "medium": "media", "low": "baja",
            # categories
            "functional": "funcional", "negative": "negativo", "validation": "validación",
            "security": "seguridad", "usability": "usabilidad", "accessibility": "accesibilidad",
            # page types
            "login": "inicio de sesión", "signup": "registro", "checkout": "pago", "product": "producto",
            "listing": "listado", "form": "formulario", "content": "contenido",
        },
    },
}


def texts(language: str) -> dict:
    """Texts for a language; unknown languages fall back to English."""
    return REPORT_TEXTS.get(language, REPORT_TEXTS["en"])


def display(value: str | None, language: str) -> str:
    """A stored code (priority, category, page type) as a person reads it."""
    if not value:
        return ""
    return texts(language)["values"].get(value, value)
