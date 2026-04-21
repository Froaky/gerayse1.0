# Gerayse Testing Playbook

## 1. Matriz por tipo de cambio

### Servicio o dominio

- agregar test de servicio para la regla principal
- agregar al menos un caso invalido si existe guardrail
- agregar caso de permisos si el servicio recibe `actor`
- aserciones:
  - registros creados o bloqueados
  - montos exactos
  - estados finales
  - relaciones y trazabilidad

### Modelo o schema

- cubrir `full_clean()` o constraint si la regla vive en modelo
- si hay migracion:
  - crear dato legacy
  - migrar
  - verificar resultado real
- revisar fixtures historicos que puedan romper por nuevos campos obligatorios

### Formulario o vista

- cubrir GET si importa permisos o visibilidad
- cubrir POST valido
- cubrir POST invalido
- cubrir usuario sin permiso si aplica
- para listas y dashboards, cubrir filtros y acciones visibles/no visibles

### Template o copy operativo

- testear solo el copy que cambia comportamiento o evita error operativo
- si una opcion deja de existir, usar `assertNotContains`
- no hacer snapshot gigante de HTML completo

### Management command

- cubrir `dry-run` sin persistencia
- cubrir `apply` con persistencia
- verificar stdout relevante
- verificar efectos colaterales en DB

### Migracion o compatibilidad legacy

- crear datos con estado historico correcto
- si el modelo actual no sirve para crear ese estado, usar `old_apps` o IDs directos
- no mezclar schema historico con modelos actuales sin pensarlo

## 2. Prioridades de cobertura

1. riesgo de dinero, estado o auditoria
2. riesgo de permisos
3. riesgo de regresion del bug reportado
4. riesgo de filtro, agregacion o rango
5. riesgo de UI operativa visible al usuario

## 3. Secuencia sugerida de ejecucion

1. correr la suite mas chica que toca la regla central
2. si cambia contrato de modulo, correr regresion de ese modulo
3. si toca datos compartidos, correr suites vecinas
4. no vender "verde" con una suite que no toca el cambio

## 4. Suites utiles del repo

- `python manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests -v 2`
- `python manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests cashops.tests_commands cashops.test_migration_safety -v 2`
- `python manage.py test users.tests -v 2`
- `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests treasury.tests_ep05 -v 2`
- `python manage.py test treasury.tests.TreasuryAdminProtectionTests treasury.tests.TreasuryPermissionTests -v 2`
- `python manage.py test cashops.tests cashops.tests_commands cashops.test_migration_safety users.tests core.tests -v 1`

## 5. Aserciones que importan en este repo

- `Decimal("...")` para montos
- `ValidationError` con campo correcto
- `PermissionDenied` o `403` para acceso indebido
- `404` cuando el flujo ya no debe existir
- conteos y filtros por `sucursal`, `fecha_operativa`, rango o alcance
- compatibilidad con usuarios o datos legacy

## 6. Matriz rapida por app

- `cashops`:
  - servicio para saldo, traspaso, egreso, arrastre
  - vista para filtros, ocultamiento de acciones y permisos
  - migracion cuando cambien datos obligatorios de sucursal o caja
- `treasury`:
  - servicio para pagos, deuda, disponibilidades, alertas y clasificaciones
  - vista para dashboards, filtros por fecha y sucursal, permisos
  - suites de permisos cuando cambie acceso administrativo
- `users`:
  - vista y formulario para alta, edicion, listas y busqueda
  - compatibilidad con usuarios legacy
  - permisos por `role`

## 7. Errores comunes a evitar

- cubrir una validacion de dominio solo desde la vista
- testear copy sin testear efecto persistido
- crear fixtures irreales que esquivan la regla real
- ignorar datos legacy cuando se agrega un campo obligatorio
- declarar "todo verde" corriendo una suite chica que no toca el cambio real

## 8. Stop-ship

- suite corrida no toca el cambio central
- regla de negocio sin asercion de negocio
- solo hay happy path
- no se distinguio deuda previa de regresion nueva

## 9. Criterio de done para testing

- hay al menos una prueba que fallaria si se revierte la regla importante
- la capa correcta esta cubierta
- la suite corrida fue informada
- los riesgos no cubiertos quedaron explicitados
