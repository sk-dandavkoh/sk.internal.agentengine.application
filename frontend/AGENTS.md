# Frontend Engineering Guidelines

## 1. Geltungsbereich

Diese Datei ergänzt die globalen Regeln aus `/AGENTS.md` um verbindliche Regeln für das Frontend.

Vor jeder Frontend-Arbeit sind mindestens diese Dokumente zu lesen:

1. `/AGENTS.md`
2. `/frontend/AGENTS.md`
3. `/docs/vision.md`
4. `/docs/architecture.md`
5. `/docs/roadmap-frontend.md`

Bei Widersprüchen gilt:

1. explizite Projektanforderung
2. `/AGENTS.md`
3. `/frontend/AGENTS.md`
4. `/docs/architecture.md`
5. `/docs/vision.md`
6. `/docs/roadmap-frontend.md`

Diese Datei wiederholt bewusst keine allgemeinen Regeln aus `/AGENTS.md`.

---

# 2. Frontend-Ziel

Das Frontend ist die Benutzeroberfläche der Agent Engine.

Es ist verantwortlich für:

- Navigation
- Workflowübersicht
- Workflowverwaltung
- visuellen Graph Editor
- Node Library
- Node Configuration Inspector
- Graph Validation UX
- Publish Workflow
- Workflow Versions
- Execution UI
- Live Execution
- Execution Inspector
- Logs
- Monitoring
- Credentials UI
- Trigger UI
- Reports
- Plugin Management
- Settings
- Authentication
- Developer Experience

Das Backend bleibt die autoritative Quelle für:

- Domain State
- Workflowstatus
- Graphvalidierung
- Berechtigungen
- Credentials
- Execution State
- Node Contracts
- Versionen
- Security Rules

Das Frontend darf diese Regeln für UX-Zwecke spiegeln, aber niemals als alleinige fachliche Instanz behandeln.

---

# 3. Technologieprinzipien

Das Frontend verwendet grundsätzlich:

- React
- TypeScript
- Vite
- Tailwind CSS
- React Router
- TanStack Query
- React Flow bzw. die im Projekt festgelegte Graph-Editor-Library
- Zod oder eine vergleichbare Schema-/Runtime-Validation-Lösung
- WebSocket für Live-Updates
- Vitest
- React Testing Library
- Playwright
- ESLint
- Prettier
- TypeScript Strict Mode

Konkrete Versionen werden nicht geraten oder ungeprüft eingeführt.

Neue Dependencies benötigen einen nachvollziehbaren architektonischen Nutzen.

---

# 4. TypeScript

TypeScript Strict Mode ist verpflichtend.

Kein unnötiges:

```ts
any
```

Keine pauschalen Type Assertions:

```ts
value as SomeType
```

wenn dadurch echte Unsicherheit lediglich verborgen wird.

Bevorzugt:

-   explizite Typen
    
-   Discriminated Unions
    
-   generische Typen
    
-   Runtime Validation an externen Grenzen
    
-   Narrowing
    
-   Type Guards
    
-   aus Schemas abgeleitete Typen, sofern sinnvoll
    

API-Daten gelten nicht automatisch als vertrauenswürdig, nur weil TypeScript sie typisiert.

----------

# 5. Frontend-Architektur

Das Frontend muss klar zwischen folgenden Zuständen unterscheiden:

```text
Server State
UI State
Editor State
Local Form State
Session/Auth State
```

Diese Zustände dürfen nicht unkontrolliert vermischt werden.

Beispiel:

```text
Backend Workflow
    ↓
TanStack Query
    ↓
Feature Components
```

Der Graph Editor darf seine temporären Änderungen nicht automatisch als serverseitig veröffentlichten Zustand behandeln.

----------

# 6. Empfohlene Struktur

Die konkrete Projektstruktur kann angepasst werden, sollte aber klare Verantwortungsgrenzen besitzen.

Beispielsweise:

```text
src/
├── app/
├── components/
├── features/
│   ├── auth/
│   ├── dashboard/
│   ├── workflows/
│   ├── editor/
│   ├── nodes/
│   ├── executions/
│   ├── monitoring/
│   ├── credentials/
│   ├── triggers/
│   ├── reports/
│   ├── plugins/
│   └── settings/
├── api/
├── hooks/
├── lib/
├── schemas/
├── types/
└── styles/
```

Feature-spezifische Logik gehört möglichst in das jeweilige Feature.

Keine riesigen globalen Utility-Dateien, die fachlich völlig unterschiedliche Bereiche vermischen.

----------

# 7. Backend ist Source of Truth

Das Frontend darf keine fachlichen Zustände erfinden.

Beispielsweise darf ein Frontend nicht selbst entscheiden:

```text
Workflow is publishable
```

nur weil lokale UI-Validierung erfolgreich ist.

Es darf lokal prüfen:

```text
Looks valid
```

Die verbindliche Antwort kommt vom Backend.

Dasselbe gilt für:

-   Execution Status
    
-   Node Status
    
-   Workflow Version
    
-   Credentials
    
-   Permissions
    
-   Plugin Compatibility
    
-   Trigger State
    
-   Validation Errors
    

----------

# 8. API Layer

Direkte `fetch`-Aufrufe in Feature-Komponenten sind grundsätzlich zu vermeiden.

Nicht:

```tsx
function WorkflowPage() {
  const data = await fetch("/api/v1/workflows");
}
```

Stattdessen:

```text
Component
    ↓
Feature Hook / Query
    ↓
API Client
    ↓
Backend
```

Der API Client kapselt:

-   Base URL
    
-   Authentication
    
-   Headers
    
-   Error Handling
    
-   Serialization
    
-   Request IDs / Correlation IDs, sofern relevant
    
-   Response Parsing
    

----------

# 9. API Client

Der API Client muss zentralisiert sein.

Beispielsweise:

```text
src/api/
├── client.ts
├── workflows.ts
├── executions.ts
├── nodes.ts
├── credentials.ts
├── triggers.ts
├── reports.ts
└── plugins.ts
```

Die tatsächliche Struktur kann variieren.

Wichtig ist:

> Feature-Komponenten sollen nicht wissen, wie HTTP Requests technisch ausgeführt werden.

----------

# 10. API Runtime Validation

TypeScript-Typen allein validieren keine Serverantworten.

Für kritische API-Grenzen soll Runtime Validation verwendet werden, insbesondere bei:

-   komplexen Backend-Responses
    
-   Plugin-Manifests
    
-   Node Contracts
    
-   Workflow Packages
    
-   Execution Events
    
-   WebSocket Messages
    

Ungültige Serverdaten müssen kontrolliert behandelt werden.

Nicht einfach:

```ts
return response.json() as Workflow;
```

wenn die Struktur nicht anderweitig garantiert ist.

----------

# 11. TanStack Query

Server State wird grundsätzlich über eine dedizierte Server-State-Lösung verwaltet.

TanStack Query ist hierfür vorgesehen.

Verantwortlichkeiten:

-   Fetching
    
-   Caching
    
-   Refetching
    
-   Invalidation
    
-   Mutation State
    
-   Loading State
    
-   Error State
    

Nicht unnötig denselben Server State zusätzlich in einen globalen Client Store kopieren.

----------

# 12. Query Keys

Query Keys müssen stabil und semantisch sein.

Beispielsweise:

```ts
["workflows"]
["workflows", workflowId]
["workflows", workflowId, "versions"]
["executions", executionId]
```

Keine zufälligen oder inkonsistenten Keys.

Mutation-Invalidierungen müssen explizit definiert werden.

----------

# 13. Server State vs. UI State

Beispiel:

```text
Server State:
Workflow
WorkflowVersion
Execution
NodeDefinition

UI State:
SelectedNode
OpenPanel
SidebarWidth
ActiveTab
DialogOpen

Editor State:
UnsavedGraphChanges
Selection
Viewport
UndoStack
RedoStack
Clipboard
```

Diese Zustände dürfen nicht in einen einzigen globalen State-Container gepackt werden.

----------

# 14. Routing

Routing muss zentral und nachvollziehbar definiert sein.

Beispielhafte Bereiche:

```text
/login
/dashboard
/workflows
/workflows/:workflowId
/workflows/:workflowId/editor
/workflows/:workflowId/versions
/executions
/executions/:executionId
/monitoring
/credentials
/triggers
/reports
/plugins
/settings
```

Die tatsächliche Route-Struktur ergibt sich aus der Roadmap.

Geschützte Bereiche müssen zentral abgesichert werden.

----------

# 15. Application Shell

Das Application Shell ist verantwortlich für:

-   globale Navigation
    
-   Header
    
-   Sidebar
    
-   Routing
    
-   Session State
    
-   globale Notifications
    
-   globale Error Boundaries
    
-   responsive Grundstruktur
    

Das Shell darf keine fachliche Logik aller Features enthalten.

----------

# 16. Design System

UI-Komponenten sollen konsistent aufgebaut sein.

Wiederkehrende Komponenten werden zentralisiert, beispielsweise:

```text
Button
Input
Select
Checkbox
Switch
Dialog
Drawer
Tabs
Tooltip
Dropdown
Badge
Card
Table
Toast
Alert
Spinner
EmptyState
Skeleton
```

Feature-spezifische Komponenten dürfen diese Komponenten zusammensetzen.

Nicht jede Seite soll ihre eigene Button-/Dialog-/Input-Variante erfinden.

----------

# 17. Tailwind

Tailwind wird konsistent verwendet.

Keine unkontrollierte Mischung aus:

-   Inline Styles
    
-   zufälligem CSS
    
-   Tailwind
    
-   mehreren konkurrierenden Styling-Systemen
    

Ausnahmen sind erlaubt, wenn technische Gründe bestehen.

Wiederkehrende visuelle Patterns sollten über Komponenten abstrahiert werden, nicht über riesige Utility-String-Kopien.

----------

# 18. Accessibility

Accessibility ist Bestandteil der Implementierung.

Mindestens berücksichtigen:

-   semantisches HTML
    
-   Tastaturbedienung
    
-   Fokusmanagement
    
-   sichtbarer Focus State
    
-   Labels
    
-   ARIA nur wenn erforderlich
    
-   Dialog-Fokus
    
-   Screenreader-Verhalten
    
-   ausreichende Kontraste
    
-   Fehlermeldungen
    
-   Tabellen-/Listen-Semantik
    

Ein UI gilt nicht als fertig, wenn es nur mit Maus funktioniert.

----------

# 19. Loading States

Jede asynchrone UI muss einen definierten Zustand besitzen.

Mindestens:

```text
Initial Loading
Refreshing
Submitting
Success
Empty
Error
Partial Data
```

Keine unkontrollierten weißen Flächen während API Requests.

Für komplexe Bereiche bevorzugt:

-   Skeletons
    
-   Placeholder
    
-   progressive Darstellung
    

anstatt unnötiger globaler Spinner.

----------

# 20. Error States

Fehler müssen benutzerverständlich dargestellt werden.

Technische Fehlermeldungen wie:

```text
TypeError: Cannot read properties of undefined
```

gehören nicht als primäre User Message ins UI.

Das Frontend soll zwischen folgenden Kategorien unterscheiden können:

-   Validation Error
    
-   Authentication Error
    
-   Authorization Error
    
-   Not Found
    
-   Conflict
    
-   Network Error
    
-   Backend Error
    
-   Timeout
    
-   Unknown Error
    

Technische Details können für Entwickler in geeigneten Debug-/Inspector-Bereichen sichtbar sein.

----------

# 21. Authentication

Authentication wird zentral behandelt.

Das Frontend darf Authentication State nicht an vielen Stellen separat implementieren.

Geschützte Bereiche müssen einen einheitlichen Mechanismus verwenden.

Session-/Token-Daten dürfen nicht unnötig in:

-   URLs
    
-   Logs
    
-   Analytics
    
-   Local Storage
    

geschrieben werden.

Wenn das Backend HttpOnly Cookies verwendet, dürfen diese nicht durch JavaScript ausgelesen werden.

----------

# 22. Authorization

Frontend Authorization ist UX.

Sie ersetzt niemals Backend Authorization.

Beispiel:

```text
User sieht "Publish" nicht
```

bedeutet nicht:

```text
User darf nicht publishen
```

Das Backend muss die Operation ebenfalls prüfen.

403-/Authorization-Fehler müssen im Frontend sauber behandelt werden.

----------

# 23. Secrets und Credentials UI

Credentials sind besonders sensibel.

Das Frontend darf Secrets nicht unnötig speichern.

Insbesondere nicht dauerhaft in:

-   React State länger als nötig
    
-   URL
    
-   Logs
    
-   Query Cache
    
-   Local Storage
    
-   Session Storage
    
-   Exportdaten
    

Ein Credential-Formular muss bewusst mit Secret-Werten umgehen.

Nach erfolgreicher Speicherung darf ein Secret nicht automatisch erneut vom Backend geladen und angezeigt werden, sofern das Backend dies nicht ausdrücklich vorsieht.

----------

# 24. Node Library

Die Node Library ist schema-/contract-getrieben.

Node Informationen kommen vom Backend bzw. dessen Node Registry.

Eine Node Card kann beispielsweise darstellen:

```text
Name
Description
Category
Icon
Version
Capabilities
```

Die Node Library darf nicht separat eine zweite statische Liste aller Nodes pflegen.

Neue Backend-Nodes sollen nach Möglichkeit automatisch in der UI erscheinen.

----------

# 25. Node Configuration Inspector

Der Node Inspector ist schema-driven.

Der Node Contract liefert unter anderem:

-   Inputs
    
-   Outputs
    
-   Config Schema
    
-   Required Fields
    
-   Types
    
-   Defaults
    
-   Validation
    
-   Documentation
    

Das Frontend generiert daraus geeignete Controls.

Nicht für jeden Node eine vollständig individuell programmierte Form bauen, wenn der Contract generisch darstellbar ist.

----------

# 26. Node Config Forms

Formulare müssen unterscheiden zwischen:

```text
Draft Form State
Saved Draft State
Published Version
```

Ein Nutzer kann Änderungen machen, ohne dass dadurch automatisch eine neue Workflow Version entsteht.

Save/Autosave und Publish sind unterschiedliche Operationen.

----------

# 27. Form Validation

Clientseitige Validation dient der UX.

Backend Validation bleibt autoritativ.

Clientseitige Validation soll möglichst mit denselben Schemas/Regeln arbeiten, sofern diese sicher gemeinsam nutzbar sind.

Nach einem Backend-Validation-Fehler muss das Frontend den Fehler dem entsprechenden Node/Field zuordnen können.

----------

# 28. Graph Editor

Der Graph Editor ist eine zentrale Frontend-Komponente.

Er muss unterscheiden zwischen:

```text
Persisted Draft
Unsaved Local Changes
Published Version
Execution View
```

Ein veröffentlichter Graph darf nicht versehentlich editierbar erscheinen.

----------

# 29. Graph Editor State

Der Graph Editor benötigt einen eigenen Editor State.

Dieser kann enthalten:

-   Nodes
    
-   Edges
    
-   Selection
    
-   Viewport
    
-   Undo Stack
    
-   Redo Stack
    
-   Clipboard
    
-   Drag State
    
-   Connection State
    
-   Local Validation Hints
    
-   Dirty State
    

Server State und Editor State dürfen nicht unkontrolliert vermischt werden.

----------

# 30. Undo / Redo

Der Editor benötigt nachvollziehbares Undo/Redo.

Operationen sollten als atomare Editor-Änderungen behandelbar sein.

Beispielsweise:

```text
Add Node
Move Node
Delete Node
Connect Nodes
Disconnect Nodes
Change Config
Duplicate Node
Paste Node
```

Ein Save darf nicht automatisch den Undo Stack zerstören.

----------

# 31. Autosave

Wenn Autosave implementiert wird, muss es deterministisch und robust sein.

Zu berücksichtigen:

-   Debouncing
    
-   Concurrent Saves
    
-   Save Errors
    
-   Dirty State
    
-   Revision Conflicts
    
-   Navigation während Save
    
-   Browser Refresh
    

Autosave darf nicht zu unkontrollierten Request-Schleifen führen.

----------

# 32. Graph Connections

Verbindungen müssen über den Node Contract validiert werden.

Das Frontend kann beim Verbinden bereits prüfen:

-   Port existiert
    
-   Richtung passt
    
-   Typ passt
    
-   Mehrfachverbindung erlaubt
    
-   Required Constraints
    

Das Backend muss dieselbe Verbindung beim Speichern/Validieren erneut prüfen.

----------

# 33. Graph Validation UI

Validation Errors müssen strukturiert dargestellt werden.

Ein Fehler sollte nach Möglichkeit referenzieren können:

```text
Workflow
Node
Port
Edge
Config Field
Trigger
```

Beispiel:

```text
Node "Send Email"
→ Input "recipient"
→ Required value missing
```

Klick auf einen Fehler sollte den relevanten Editor-Bereich fokussieren können.

----------

# 34. Publish Workflow

Publish ist ein expliziter Workflow.

Typischer Ablauf:

```text
Draft
 ↓
Validate
 ↓
Show Validation Result
 ↓
Publish Confirmation
 ↓
Backend creates immutable Version
 ↓
Frontend refreshes Versions
```

Das Frontend darf eine erfolgreiche lokale Validation nicht mit einem erfolgreichen Publish gleichsetzen.

----------

# 35. Workflow Versions

Workflow Versions sind immutable.

Das UI muss klar zwischen:

```text
Draft
Version 1
Version 2
Version 3
```

unterscheiden.

Eine veröffentlichte Version darf nicht wie ein editierbarer Draft aussehen.

Execution Views müssen anzeigen, auf welcher Version sie basieren.

----------

# 36. Execution UI

Execution UI zeigt den tatsächlichen Backend-State.

Mindestens:

```text
Execution ID
Workflow
Workflow Version
Trigger
Status
Start
End
Duration
Nodes
Errors
Metrics
```

Status darf nicht nur aus lokalem UI-State berechnet werden.

----------

# 37. Execution State Mapping

Backend States müssen zentral gemappt werden.

Beispiel:

```text
CREATED
QUEUED
RUNNING
PAUSED
WAITING
COMPLETED
FAILED
CANCELLED
TIMEOUT
```

Nicht an zehn Stellen eigene Statuslisten pflegen.

Eine zentrale Statusdarstellung sollte definieren:

-   Label
    
-   Icon
    
-   Semantik
    
-   erlaubte UI-Aktionen
    

----------

# 38. Live Execution

Live Execution verwendet WebSocket oder die vom Backend definierte Streaming-Technologie.

Architektur:

```text
Backend Execution
       ↓
Event Stream
       ↓
WebSocket
       ↓
Frontend Event Handler
       ↓
UI State
```

Live Events sind nicht automatisch die alleinige Wahrheit.

Das Frontend muss jederzeit auf den autoritativen Backend-State zurückfallen können.

----------

# 39. WebSocket Robustness

WebSocket-Verbindungen müssen behandeln:

-   initial connect
    
-   reconnect
    
-   disconnect
    
-   duplicate events
    
-   out-of-order events
    
-   stale events
    
-   authentication expiry
    
-   server restart
    
-   malformed messages
    

Nicht davon ausgehen, dass Events immer genau einmal und in perfekter Reihenfolge eintreffen.

----------

# 40. WebSocket Event Validation

Eingehende Events müssen validiert werden.

Nicht:

```ts
const event = message as ExecutionEvent;
```

ohne Runtime-Prüfung.

Unbekannte Event-Typen sollen kontrolliert ignoriert oder als diagnostizierbarer Fehler behandelt werden.

----------

# 41. Live State Reconciliation

Wenn Live Events und API State auseinanderlaufen:

```text
WebSocket Event
       ↓
local state
       ↓
periodic/refetch authoritative state
```

Der Backend-State gewinnt.

Nach Reconnect sollte eine Synchronisation erfolgen.

----------

# 42. Execution Inspector

Der Execution Inspector muss eine Execution nachvollziehbar machen.

Er sollte unter anderem darstellen können:

-   Execution Metadata
    
-   Graph
    
-   Node States
    
-   Node Inputs
    
-   Node Outputs
    
-   Logs
    
-   Errors
    
-   Timings
    
-   Retry Information
    
-   Token Usage
    
-   Provider Information, soweit zulässig
    

Secrets dürfen niemals sichtbar werden.

----------

# 43. Node Execution Details

Node Inputs/Outputs müssen secret-safe dargestellt werden.

Das UI muss berücksichtigen:

```text
secret
credential reference
sensitive value
binary artifact
large output
```

Große Outputs dürfen nicht ungefiltert den gesamten Browser-Speicher belasten.

----------

# 44. Logs UI

Logs müssen:

-   filterbar
    
-   zeitlich sortierbar
    
-   Execution/Node zuordenbar
    
-   durchsuchbar
    
-   secret-safe
    

sein.

Bei sehr großen Logmengen muss Pagination/Virtualisierung verwendet werden.

----------

# 45. Monitoring

Monitoring ist eine separate Perspektive auf:

-   Executions
    
-   Laufzeiten
    
-   Fehler
    
-   Node Performance
    
-   Trigger
    
-   Provider Usage
    
-   Token Usage
    
-   Ressourcen
    

Monitoring darf nicht ausschließlich auf lokalem Browser-State basieren.

----------

# 46. Charts

Charts müssen:

-   korrekte Daten verwenden
    
-   Einheiten eindeutig anzeigen
    
-   Zeiträume eindeutig darstellen
    
-   Loading/Error/Empty States besitzen
    
-   bei großen Datenmengen performant bleiben
    

Keine irreführenden Visualisierungen.

Keine künstlich erfundenen Daten, nur damit ein Chart gefüllt aussieht.

----------

# 47. Reports UI

Reports müssen aus Backend-Daten dargestellt werden.

Das Frontend darf keine kritischen Reportdaten selbst rekonstruieren, wenn das Backend einen autoritativen Report liefert.

Export-Aktionen müssen eindeutig sein:

```text
View
Download
Export
```

Keine Secrets in Client-seitig erzeugten Exporten.

----------

# 48. Triggers UI

Trigger UI muss die verschiedenen Trigger-Typen klar unterscheiden:

```text
Manual
API
Cron
Interval
Webhook
Event
```

Konfiguration wird schema-/backend-getrieben dargestellt, soweit möglich.

Das Frontend darf keine Trigger-Konfiguration als gültig behandeln, wenn das Backend sie ablehnt.

----------

# 49. Credentials UI

Credentials UI muss mindestens unterscheiden:

```text
Credential Exists
Credential Configured
Credential Secret
Credential Metadata
```

Ein vorhandenes Credential bedeutet nicht, dass dessen Secret erneut sichtbar ist.

Delete/Rotate/Update-Aktionen benötigen klare Bestätigung, wenn destruktiv.

----------

# 50. Plugin Management UI

Plugins können darstellen:

-   Name
    
-   Version
    
-   Status
    
-   Compatibility
    
-   Dependencies
    
-   Nodes
    
-   Documentation
    
-   Errors
    

Install/Disable/Remove sind serverseitige Aktionen.

Das Frontend darf Plugin-Kompatibilität nicht selbst als alleinige Instanz bewerten.

----------

# 51. Import / Export UI

Import muss:

1.  Datei auswählen
    
2.  Upload/Parsing starten
    
3.  Backend validieren lassen
    
4.  Validation Result anzeigen
    
5.  Konflikte darstellen
    
6.  Import bestätigen
    
7.  Ergebnis anzeigen
    

Export muss klar machen, dass Credentials nicht Bestandteil des Exports sind.

----------

# 52. File Uploads

Datei-Uploads benötigen:

-   Typprüfung
    
-   Größenlimit
    
-   Fehlerbehandlung
    
-   Progress, sofern sinnvoll
    
-   Abbruchmöglichkeit, sofern sinnvoll
    

Frontend-Dateitypprüfung ist keine Security Boundary.

Das Backend muss Uploads ebenfalls validieren.

----------

# 53. Notifications

Notifications sollen konsistent sein.

Geeignete Kategorien:

```text
Success
Info
Warning
Error
```

Keine dauerhafte Flut von Toasts bei wiederkehrenden Live Events.

Für komplexe Fehler sind Inline-Fehler oder Error Panels geeigneter.

----------

# 54. Modals und Destructive Actions

Destruktive Aktionen benötigen klare UI-Bestätigung, beispielsweise:

-   Workflow Delete
    
-   Version Delete, sofern erlaubt
    
-   Credential Delete
    
-   Plugin Remove
    
-   Trigger Delete
    

Bestätigungsdialoge müssen klar benennen:

-   was passiert
    
-   welche Ressource betroffen ist
    
-   ob die Aktion reversibel ist
    

----------

# 55. Unsaved Changes

Wenn ein Editor ungespeicherte Änderungen besitzt, muss Navigation entsprechend behandelt werden.

Mögliche Fälle:

```text
Save
Discard
Cancel
```

Browser Refresh/Close muss berücksichtigt werden, sofern technisch möglich.

----------

# 56. Optimistic Updates

Optimistic Updates nur verwenden, wenn:

-   Operation reversibel oder kontrollierbar ist
    
-   Konflikte sauber behandelt werden
    
-   Backend-State anschließend synchronisiert wird
    

Nicht für riskante oder komplexe Domainoperationen blind optimistisch aktualisieren.

Besonders vorsichtig bei:

-   Publish
    
-   Delete
    
-   Execution Start
    
-   Credential Changes
    
-   Plugin Installation
    

----------

# 57. Race Conditions

Frontend muss konkurrierende Requests berücksichtigen.

Beispiele:

```text
User changes node config
        ↓
Save A
        ↓
Save B
```

Antwort A darf nicht nach Antwort B den neueren Zustand überschreiben.

Geeignete Mechanismen:

-   Request cancellation
    
-   Mutation sequencing
    
-   revision IDs
    
-   optimistic concurrency
    
-   server-authoritative refetch
    

----------

# 58. Performance

Performance ist insbesondere beim Graph Editor relevant.

Zu beachten:

-   Rendering großer Graphen
    
-   Node Count
    
-   Edge Count
    
-   Selection Updates
    
-   Dragging
    
-   Zoom/Pan
    
-   Logs
    
-   Execution Events
    
-   Monitoring Data
    
-   Large Outputs
    

Keine unnötigen globalen Re-Renders.

----------

# 59. React Performance

Komponenten sollen klar definierte Verantwortlichkeiten besitzen.

Vermeiden:

-   riesige Komponenten
    
-   unnötige Context-Kaskaden
    
-   globale State Updates für lokale UI-Änderungen
    
-   instabile Callback-/Object-Erzeugung, wenn sie messbar problematisch ist
    

Memoization nicht blind einsetzen.

Zuerst messen, dann optimieren.

----------

# 60. Graph Performance

Der Graph Editor muss mit realistischen Graphgrößen getestet werden.

Zu testen sind mindestens:

-   viele Nodes
    
-   viele Edges
    
-   viele Labels
    
-   häufige Selection Changes
    
-   schnelle Drag Events
    
-   Zoom/Pan
    
-   laufende Execution Events
    

Bei großen Graphen sind geeignete Rendering-/Virtualisierungsstrategien einzusetzen.

----------

# 61. Large Data

Große Datenmengen dürfen nicht ungefiltert in den Browser geladen werden.

Insbesondere:

-   Logs
    
-   Execution History
    
-   Node Outputs
    
-   Reports
    
-   Monitoring Time Series
    

verwenden nach Möglichkeit:

-   Pagination
    
-   Cursor Pagination
    
-   Filtering
    
-   Sorting
    
-   Virtualization
    
-   Lazy Loading
    

----------

# 62. Browser Storage

Local Storage/Session Storage nur bewusst verwenden.

Nicht speichern:

-   API Keys
    
-   Passwords
    
-   Access Tokens, wenn eine sicherere Architektur vorhanden ist
    
-   Credential Secrets
    
-   vertrauliche Execution Outputs
    

UI Preferences können geeignet sein, beispielsweise:

```text
Sidebar Width
Theme
Editor Preferences
```

----------

# 63. Frontend Security

Mindestens berücksichtigen:

-   XSS
    
-   CSRF
    
-   Token Handling
    
-   URL Injection
    
-   unsafe HTML
    
-   file uploads
    
-   download handling
    
-   iframe security
    
-   open redirects
    
-   WebSocket authentication
    
-   sensitive data exposure
    

Keine ungeprüfte HTML-Injektion.

Insbesondere:

```tsx
dangerouslySetInnerHTML
```

nur mit expliziter Begründung und geeigneter Sanitization verwenden.

----------

# 64. User-Generated Content

User-generated Inhalte können enthalten:

-   Prompts
    
-   Node Config
    
-   Workflow Names
    
-   Descriptions
    
-   Logs
    
-   External Search Results
    
-   LLM Outputs
    

Diese Inhalte sind nicht automatisch vertrauenswürdig.

Keine ungeprüfte Interpretation als:

-   HTML
    
-   JavaScript
    
-   System Instruction
    
-   UI Configuration
    

----------

# 65. LLM Output

LLM Outputs sind untrusted data.

Wenn Markdown gerendert wird:

-   geeigneten Markdown Renderer verwenden
    
-   HTML Sanitization berücksichtigen
    
-   keine unkontrollierte Script-Ausführung
    

LLM Output darf nicht automatisch als Frontend-Code oder UI-Konfiguration ausgeführt werden.

----------

# 66. External Links

Externe Links aus:

-   Search Results
    
-   Plugins
    
-   Documentation
    
-   User Content
    

müssen sicher behandelt werden.

Keine unkontrollierten `javascript:`-URLs.

Bei neuen Tabs geeignete Security-Attribute verwenden.

----------

# 67. WebSocket Security

WebSocket-Verbindungen müssen authentifiziert und autorisiert sein.

Ein Benutzer darf nicht allein durch Kenntnis einer Execution ID deren Live Events abonnieren können.

Backend Authorization bleibt maßgeblich.

----------

# 68. Tests

Frontend-Features benötigen Tests auf der passenden Ebene.

Mindestens relevant:

```text
Unit Tests
Component Tests
Hook Tests
API/Contract Tests
Graph Editor Tests
Accessibility Tests
E2E Tests
```

Nicht jedes Feature benötigt jede Ebene.

Kritische User Journeys müssen jedoch durch E2E-Tests abgesichert werden.

----------

# 69. Vitest

Vitest wird für Unit- und Component-Tests verwendet.

Zu testen sind insbesondere:

-   Utilities
    
-   State Logic
    
-   Query Hooks
    
-   Validation
    
-   Graph Transformations
    
-   Node Config Forms
    
-   Event Handling
    
-   UI Components
    

Tests sollen Verhalten testen, nicht interne Implementierungsdetails.

----------

# 70. React Testing Library

React Testing Library bevorzugt User-Verhalten.

Bevorzugt:

```text
find element
interact
assert visible behavior
```

Nicht bevorzugt:

```text
assert internal component state
assert private implementation detail
```

Tests sollen möglichst nahe an realer Nutzung liegen.

----------

# 71. Graph Editor Tests

Der Graph Editor benötigt gezielte Tests für:

-   Add Node
    
-   Delete Node
    
-   Move Node
    
-   Connect Nodes
    
-   Disconnect Nodes
    
-   Invalid Connection
    
-   Config Change
    
-   Undo
    
-   Redo
    
-   Copy
    
-   Paste
    
-   Selection
    
-   Save
    
-   Autosave
    
-   Validation
    
-   Publish
    
-   Unsaved Changes
    

----------

# 72. API Mocking

Mocks dürfen für isolierte Frontend-Tests verwendet werden.

Sie dürfen jedoch nicht die einzigen Tests der Integration darstellen.

API Contract Tests müssen sicherstellen, dass Frontend und Backend denselben Vertrag verwenden.

Keine frei erfundenen Mock Responses, die vom echten Backend nicht unterstützt werden.

----------

# 73. Playwright E2E

Playwright ist für echte User Journeys vorgesehen.

Mindestens müssen wichtige Flows abgedeckt werden:

```text
Login
 ↓
Dashboard
 ↓
Create Workflow
 ↓
Open Editor
 ↓
Add Node
 ↓
Configure Node
 ↓
Connect Nodes
 ↓
Save Draft
 ↓
Validate
 ↓
Publish
 ↓
Start Execution
 ↓
Observe Execution
 ↓
Inspect Logs
```

Weitere wichtige Flows:

```text
Import / Export
Credentials
Triggers
Reports
Plugins
```

----------

# 74. E2E Test Environment

E2E Tests sollen gegen eine reproduzierbare Umgebung laufen.

Idealerweise:

```text
Frontend
+
Backend
+
PostgreSQL
+
Redis, falls erforderlich
```

Keine Abhängigkeit von einer zufällig laufenden persönlichen Entwicklungsumgebung.

Testdaten müssen isoliert sein.

----------

# 75. Keine Sleep-basierten Frontend-Tests

Nicht:

```ts
await page.waitForTimeout(5000);
```

nur damit "irgendwann" etwas geladen ist.

Stattdessen:

-   `expect(...).toBeVisible()`
    
-   `waitFor`
    
-   Netzwerkzustände
    
-   definierte UI-Zustände
    
-   explizite Test Hooks, falls erforderlich
    

Tests dürfen nicht von zufälligem Timing abhängen.

----------

# 76. Accessibility Tests

Wichtige Komponenten und User Journeys müssen auf Accessibility geprüft werden.

Besonders:

-   Dialoge
    
-   Forms
    
-   Navigation
    
-   Node Inspector
    
-   Graph Editor Controls
    
-   Tabellen
    
-   Notifications
    

----------

# 77. Responsive Design

Die Anwendung soll auf relevanten Bildschirmgrößen funktionieren.

Besonders kritisch:

-   Dashboard
    
-   Workflow List
    
-   Execution Inspector
    
-   Settings
    

Der Graph Editor darf für kleine Displays eine angepasste UX besitzen.

Nicht jede Desktop-Funktion muss unverändert auf Mobile übertragen werden.

----------

# 78. Browser Compatibility

Unterstützte Browser müssen explizit definiert werden.

Browser-spezifische Workarounds nur mit nachvollziehbarem Grund.

Keine experimentellen APIs ohne Fallback oder definierte Support-Anforderung.

----------

# 79. Internationalisierung

Wenn Internationalisierung später vorgesehen ist, dürfen UI-Texte nicht unnötig über das gesamte Projekt verstreut und schwer ersetzbar sein.

Wenn V1 zunächst nur eine Sprache unterstützt, muss dies bewusst und konsistent umgesetzt werden.

Datums-, Zeit- und Zahlenformatierung soll zentral erfolgen.

----------

# 80. Datum und Zeit

Backend-Daten sollten als eindeutige Zeitwerte verarbeitet werden.

Das Frontend darf Zeitstempel nicht über lokale String-Manipulationen interpretieren.

Zeitzonen müssen explizit behandelt werden.

Beispielsweise:

```text
Backend:
UTC / ISO 8601

Frontend:
locale-aware display
```

----------

# 81. Accessibility und Keyboard Shortcuts

Der Graph Editor kann Keyboard Shortcuts unterstützen.

Beispielsweise:

```text
Delete
Undo
Redo
Copy
Paste
Save
```

Shortcuts dürfen nicht normale Browser-/Assistive-Funktionen unerwartet überschreiben.

Textfelder müssen beispielsweise weiterhin normale Tastaturinteraktion unterstützen.

----------

# 82. Clipboard

Copy/Paste im Graph Editor muss strukturiert und validiert sein.

Clipboard-Daten dürfen nicht blind als gültiger Graph akzeptiert werden.

Zu berücksichtigen:

-   Node IDs
    
-   Edge IDs
    
-   Referenzen
    
-   Credentials
    
-   externe Ressourcen
    
-   Versionen
    

Secrets dürfen niemals in Clipboard-Daten gelangen.

----------

# 83. Frontend Observability

Frontend-Fehler müssen nachvollziehbar sein.

Relevant:

-   Route
    
-   User Session Context, soweit datenschutzkonform
    
-   Request/Correlation ID
    
-   Workflow ID
    
-   Execution ID
    
-   Fehlerklasse
    

Keine Secrets oder vollständigen Credential-Daten erfassen.

----------

# 84. Logging

Keine Debug-Logs in Production:

```ts
console.log(secret);
console.log(apiResponse);
```

Insbesondere nicht:

-   Tokens
    
-   Credentials
    
-   Passwords
    
-   vollständige Auth Responses
    
-   sensible Execution Outputs
    

Falls Logging verwendet wird, soll es kontrolliert und strukturiert sein.

----------

# 85. Error Boundaries

Kritische UI-Bereiche sollen durch Error Boundaries geschützt werden.

Ein Fehler in:

```text
Monitoring
```

darf nicht zwingend die komplette Anwendung unbrauchbar machen.

Error UI soll:

-   verständlich
    
-   recoverable, wenn möglich
    
-   diagnostizierbar
    

sein.

----------

# 86. Unsaved State Recovery

Wenn Editor State verloren gehen könnte, muss eine sinnvolle Strategie definiert sein.

Mögliche Mechanismen:

-   Draft Autosave
    
-   Revisioning
    
-   Restore Dialog
    
-   Unsaved Changes Warning
    

Nicht heimlich sensible Workflow-Daten dauerhaft im Browser speichern.

----------

# 87. Frontend Build

Production Build muss reproduzierbar sein.

Mindestens prüfen:

```bash
npm run build
npm run typecheck
npm run lint
npm run test
```

bzw. die tatsächlich im Projekt definierten Commands.

Keine Build Warnings ignorieren, wenn sie auf reale Probleme hinweisen.

----------

# 88. Bundle Size

Unnötige Dependencies und große Bundles vermeiden.

Besonders prüfen:

-   Graph Editor
    
-   Charts
    
-   Markdown Renderer
    
-   Icon Libraries
    
-   Syntax Highlighting
    
-   große Utility Libraries
    

Bei Bedarf Code Splitting/Lazy Loading verwenden.

----------

# 89. Lazy Loading

Große oder selten verwendete Bereiche können lazy geladen werden.

Beispielsweise:

-   Plugin Management
    
-   Reports
    
-   Monitoring
    
-   Settings
    
-   komplexe Editor-Erweiterungen
    

Lazy Loading darf jedoch nicht zu einer schlechten Kern-UX führen.

----------

# 90. Accessibility von Custom Controls

Custom UI Controls müssen sich wie echte Controls verhalten.

Wenn ein `<div>` wie ein Button funktioniert, ist das grundsätzlich ein Warnsignal.

Bevorzugt:

```html
<button>
```

statt:

```html
<div onclick="...">
```

----------

# 91. Tabellen

Tabellen für strukturierte Daten müssen:

-   semantisch korrekt
    
-   sortierbar, wenn erforderlich
    
-   filterbar, wenn erforderlich
    
-   keyboard-nutzbar
    
-   performant
    

sein.

Bei großen Datenmengen Virtualisierung oder serverseitige Pagination verwenden.

----------

# 92. Forms

Forms müssen klare Zustände besitzen:

```text
Idle
Dirty
Submitting
Success
Error
Disabled
```

Submit darf nicht mehrfach unkontrolliert ausgelöst werden.

Validation Errors müssen dem Feld zugeordnet werden.

----------

# 93. Destructive Operations

Delete/Remove-Aktionen dürfen nicht nur über lokale UI-State-Änderungen simuliert werden.

Nach erfolgreicher Backend-Operation muss der Server-State synchronisiert werden.

Bei Fehler:

```text
UI darf nicht so aussehen, als sei die Ressource gelöscht.
```

----------

# 94. Workflow Editor und Backend Revisions

Wenn das Backend Revisionen/Optimistic Concurrency unterstützt, muss das Frontend diese verwenden.

Ein Save gegen einen veralteten Draft darf nicht stillschweigend neuere Änderungen überschreiben.

Conflict UI muss verständlich sein.

----------

# 95. Feature Boundaries

Features sollen möglichst eigenständig bleiben.

Beispielsweise:

```text
workflows/
editor/
executions/
credentials/
triggers/
reports/
plugins/
```

Ein Feature darf andere Features über klar definierte Schnittstellen verwenden.

Keine zyklischen Abhängigkeiten zwischen Feature-Modulen.

----------

# 96. Shared Components

Shared Components dürfen keine fachliche Logik enthalten, die nur zu einem Feature gehört.

Beispiel:

```text
Button
Dialog
DataTable
```

gehören in Shared/UI.

Ein:

```text
WorkflowPublishDialog
```

gehört in das Workflow/Publish Feature.

----------

# 97. State Management

Ein globaler State Store wird nur eingesetzt, wenn der State tatsächlich global und clientseitig ist.

Nicht:

```text
alles in Zustand/Redux
```

nur weil ein State Store verfügbar ist.

Server State bleibt Server State.

Editor State bleibt Editor State.

Lokale UI-Zustände bleiben möglichst lokal.

----------

# 98. React Context

Context sparsam einsetzen.

Geeignet für:

-   Theme
    
-   Session Context
    
-   stabile globale Konfiguration
    

Nicht als Ersatz für sämtliche State-Management-Probleme verwenden.

Große Context Values können unnötige Re-Renders verursachen.

----------

# 99. URL State

Filter, Tabs und andere navigierbare Zustände können sinnvoll in der URL liegen.

Beispielsweise:

```text
/executions?status=failed
```

Keine sensiblen Daten in URLs.

URLs sollen teilbar und nachvollziehbar sein, sofern der betreffende Zustand dies unterstützt.

----------

# 100. Accessibility bei Graphen

Der visuelle Graph ist naturgemäß schwierig zugänglich.

Deshalb muss es für wichtige Graph-Informationen zusätzliche zugängliche Darstellungen geben, beispielsweise:

-   Node List
    
-   Node Details
    
-   Selected Node Inspector
    
-   Connection Information
    
-   Execution Status
    

Nicht ausschließlich auf visuelle Positionierung verlassen.

----------

# 101. Execution Graph

Wenn ein Graph während einer Execution dargestellt wird, soll das Frontend nicht versuchen, die Runtime selbst zu simulieren.

Es visualisiert:

```text
Backend Graph
+
Backend Node States
+
Backend Events
```

Nicht:

```text
Frontend guessed execution
```

----------

# 102. Live Event Ordering

Events können verspätet oder doppelt eintreffen.

Frontend Event Handler müssen deshalb nach Möglichkeit anhand von:

-   Event ID
    
-   Sequence Number
    
-   Timestamp
    
-   Execution ID
    
-   Node Execution ID
    

arbeiten, sofern das Backend diese Informationen bereitstellt.

----------

# 103. Stale Data

Das Frontend muss erkennen können, wenn Daten möglicherweise veraltet sind.

Beispielsweise:

```text
WebSocket disconnected
```

Dann darf die UI nicht dauerhaft suggerieren:

```text
Live
```

wenn keine Live-Verbindung besteht.

Status soll klar erkennbar sein.

----------

# 104. Offline / Network Failure

Bei temporärem Netzwerkverlust:

-   UI nicht unnötig zerstören
    
-   Fehler anzeigen
    
-   Retry ermöglichen
    
-   stale Daten klar kennzeichnen
    
-   nach Reconnect synchronisieren
    

Bei nicht speicherbaren Editor-Änderungen muss der Nutzer wissen, ob Daten sicher gespeichert wurden.

----------

# 105. Frontend Security Boundary

Das Frontend ist keine Security Boundary.

Alles, was sicherheitsrelevant ist, muss serverseitig geprüft werden.

Insbesondere:

```text
Role
Permission
Workflow Access
Credential Access
Execution Access
Plugin Actions
Trigger Actions
Import
Export
```

----------

# 106. No Business Logic Duplication

Frontend darf notwendige UI-Logik enthalten.

Es darf aber nicht eine zweite Backend-Domain implementieren.

Nicht:

```text
Backend:
Publish requires A + B + C

Frontend:
Publish requires A + B
```

Solche Unterschiede müssen vermieden oder klar als reine UX-Vorprüfung behandelt werden.

----------

# 107. Contract Synchronisation

Wenn Backend Contracts geändert werden:

-   API Types aktualisieren
    
-   Runtime Schemas aktualisieren
    
-   UI Mapping prüfen
    
-   Tests aktualisieren
    
-   E2E prüfen
    

Keine stillen API-Drift.

----------

# 108. Feature Flags

Feature Flags nur verwenden, wenn ein realer Anwendungsfall besteht.

Keine dauerhaft versteckten halbfertigen Features ohne dokumentierte Strategie.

----------

# 109. Browser Errors

Unhandled Promise Rejections und Runtime Errors dürfen nicht unbemerkt bleiben.

Kritische Fehler müssen:

-   Error Boundary
    
-   globaler Error Handler
    
-   Logging/Observability
    

je nach Art erreichen.

----------

# 110. Testing Definition of Done

Eine Frontend-Phase ist erst abgeschlossen, wenn:

-   Feature vollständig implementiert
    
-   TypeScript erfolgreich
    
-   Linter erfolgreich
    
-   Formatter erfolgreich
    
-   relevante Unit/Component Tests erfolgreich
    
-   relevante API/Contract Tests erfolgreich
    
-   relevante E2E Tests erfolgreich
    
-   Accessibility geprüft
    
-   Responsive Verhalten geprüft
    
-   Security-Auswirkungen geprüft
    
-   bestehende Tests weiterhin erfolgreich
    
-   Build erfolgreich
    
-   Dokumentation aktualisiert
    

----------

# 111. Pflichtprüfungen vor Abschluss

Mindestens die im Projekt definierten Commands ausführen.

Beispielsweise:

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

Zusätzlich bei E2E-relevanten Phasen:

```text
Playwright E2E
```

Bei Graph-/Execution-relevanten Phasen zusätzlich die entsprechenden Integration-/Contract-Tests.

----------

# 112. Roadmap-Disziplin

Das Frontend wird phasenweise entwickelt.

Während einer Phase:

1.  Roadmap lesen
    
2.  Scope der aktuellen Phase bestimmen
    
3.  bestehende Implementierung prüfen
    
4.  nur aktuelle Phase implementieren
    
5.  notwendige Tests schreiben
    
6.  bestehende Funktionalität nicht regressieren
    
7.  Dokumentation aktualisieren
    
8.  keine zukünftigen Phasen vorziehen
    

Keine halbfertigen "Vorbereitungen" für spätere Features, wenn dadurch unnötige Architektur entsteht.

----------

# 113. Architekturänderungen

Wenn die tatsächliche Implementierung von `/docs/architecture.md` abweichen soll, muss der Grund klar sein.

Bei einer relevanten Architekturänderung:

-   `/docs/architecture.md` aktualisieren
    
-   Roadmap prüfen
    
-   betroffene Feature-Strukturen prüfen
    
-   Tests anpassen
    

Keine stille Architekturdrift.

----------

# 114. Code Review Standard

Vor Abschluss muss der Code gedanklich und technisch aus Sicht eines Senior Frontend Engineers geprüft werden.

Fragen:

```text
Ist die Verantwortung am richtigen Ort?

Ist Server State sauber von UI State getrennt?

Ist der API Contract korrekt?

Kann ein Race Condition entstehen?

Was passiert bei Network Failure?

Was passiert bei Reload?

Was passiert bei doppeltem Klick?

Was passiert bei einem Backend-Fehler?

Was passiert bei WebSocket Disconnect?

Was passiert mit unsaved changes?

Sind Secrets ausgeschlossen?

Ist das UI keyboard-bedienbar?

Ist das Verhalten bei großen Datenmengen akzeptabel?

Sind Tests realistisch?
```

----------

# 115. Keine kosmetische Fertigstellung

Eine Seite gilt nicht als fertig, nur weil sie visuell gut aussieht.

Fertig bedeutet:

```text
UI
+
State
+
API
+
Error Handling
+
Loading
+
Empty State
+
Security
+
Accessibility
+
Tests
+
Performance
```

sind angemessen berücksichtigt.

----------

# 116. Phase Completion

Nach Abschluss einer Phase:

1.  Änderungen überprüfen
    
2.  Tests ausführen
    
3.  Typecheck ausführen
    
4.  Lint ausführen
    
5.  Build ausführen
    
6.  relevante E2E Tests ausführen
    
7.  Architektur prüfen
    
8.  Dokumentation prüfen
    
9.  keine Debug-Artefakte hinterlassen
    
10.  Abschlussbericht erstellen
    

Danach:

```text
STOP
```

Keine Implementierung der nächsten Roadmap-Phase in derselben Session.

----------

# 117. Definition of Done für Frontend-Code

Frontend-Code ist nicht fertig, wenn er lediglich im Browser funktioniert.

Er ist fertig, wenn er:

```text
fachlich korrekt
→ API-konform
→ typisiert
→ zustandsklar
→ fehlertolerant
→ sicher
→ zugänglich
→ performant
→ getestet
→ reproduzierbar
→ dokumentiert
```

ist.

Der zentrale Maßstab lautet:

> Das Frontend muss sich wie eine professionelle Anwendung verhalten – nicht wie eine Demo. Es muss auch bei Netzwerkfehlern, Backend-Fehlern, großen Datenmengen, konkurrierenden Änderungen, WebSocket-Unterbrechungen und unvollständigen Zuständen nachvollziehbar und stabil bleiben.