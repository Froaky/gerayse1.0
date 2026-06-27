# Protocolo de cambios asistidos por IA

Este documento define como debe trabajar un agente cuando el usuario pide una mejora, fix o nueva funcionalidad en Gerayse.

## Objetivo

Convertir pedidos informales en cambios tecnicos precisos, chicos, seguros y verificables.

El agente no debe limitarse a "hacer que ande". Debe proteger:

- integridad de datos;
- separacion por capas;
- trazabilidad operativa;
- seguridad y permisos;
- compatibilidad con datos legacy;
- mantenibilidad futura.

## Flujo obligatorio

### 1. Reconstruir el pedido

Identificar:

- que pantalla, flujo o indicador molesta;
- que resultado espera el usuario;
- si es bug, mejora, feature, limpieza, migracion o backlog;
- si afecta dinero, deuda, caja, banco, empresa, permisos o dashboards.

Resultado esperado: una frase precisa de objetivo.

Ejemplo:

> "Corregir el dashboard" no es suficiente. Debe convertirse en algo como: "Corregir el calculo de ventas base del snapshot economico para excluir canales marcados como `excluir_de_totales` y evitar que movimientos anulados inflen el resultado del periodo".

### 2. Ubicar fuente de verdad

Antes de tocar UI, ubicar donde debe vivir la regla:

| Tipo de regla | Lugar preferido |
| --- | --- |
| Invariante permanente de entidad | `models.py` |
| Dinero, deuda, cierres, pagos, snapshot | `services.py` |
| Validacion de input | `forms.py` |
| Permiso/restriccion de acceso | `permissions.py` + vista |
| Presentacion/copy/orden visual | `templates/` + CSS |
| Query reutilizable de lectura | servicio/helper dedicado |

Si la regla esta en el lugar equivocado, la opcion no es optima.

### 3. Elegir el corte mas chico

Un slice correcto debe tener:

- una causa raiz;
- un efecto observable;
- una suite de tests focalizada;
- una lista corta de archivos;
- cero refactors colaterales.

Si hay varias causas, separar en varios slices.

### 4. Evaluar opcion optima

Antes de implementar, clasificar la solucion:

#### OPTIMA

- Corrige causa raiz.
- Centraliza regla sensible.
- Respeta capas.
- Tiene tests proporcionales.
- No rompe legacy.
- No agrega deuda innecesaria.

#### ACEPTABLE

- Resuelve el problema con compromiso explicito.
- El compromiso queda documentado.
- No compromete datos ni seguridad.

#### NO OPTIMA

- Parchea solo la pantalla.
- Duplica formula.
- Evita tests.
- Mezcla dominios.
- Borra trazabilidad.
- Ignora legacy.
- Cambia mas de lo pedido.

### 5. Implementar con guardrails

Reglas practicas:

- Primero test que reproduce el bug o cubre la regla.
- Despues cambio minimo.
- Luego regresion proporcional.
- Finalmente documentar decision e impacto.

Evitar:

- reordenar archivos enteros;
- cambiar nombres masivamente;
- actualizar dependencias sin pedido;
- tocar settings de produccion sin motivo;
- agregar abstracciones genericas sin uso real.

### 6. Validar

Correr la suite mas chica que pruebe el cambio. Ampliar solo si el riesgo lo exige.

Orden sugerido:

1. Test focalizado del bug/regla.
2. Tests del modulo.
3. Tests de apps vecinas si hay contrato compartido.
4. `makemigrations --check --dry-run` si toca modelos.
5. `compileall` de apps tocadas.

### 7. Cerrar

La respuesta final o nota de cierre debe incluir:

- resumen del cambio;
- opcion elegida y por que;
- archivos tocados;
- tests corridos;
- riesgos pendientes;
- decisiones agregadas a `context.md`.

## Plantilla de contrato de cambio

Usar internamente esta plantilla antes de codificar:

```md
## Contrato de cambio

Pedido literal:
Intencion funcional:
Tipo: bug / feature / mejora / limpieza / migracion / backlog
Dominio principal:
Fuente de verdad:
Opcion recomendada: OPTIMA / ACEPTABLE / NO OPTIMA
Por que:
Incluye:
No incluye:
Riesgos:
Archivos probables:
Tests:
Criterios de aceptacion:
```

## Criterio de no avance

No avanzar sin aclarar o documentar supuesto cuando:

- hay dos interpretaciones que cambian datos de forma incompatible;
- el pedido implica borrar informacion historica;
- se puede afectar caja, banco, deuda o cierre sin fuente de verdad clara;
- no se sabe si el cambio aplica a una empresa, todas o un subconjunto;
- el usuario pide una accion operativa irreversible.

Si el riesgo es tecnico pero hay una opcion segura y acotada, avanzar con supuesto explicito.
