# Frontend Roadmap

## 1. Zweck

Diese Roadmap beschreibt die phasenweise Umsetzung des Frontends der Agent Orchestration Engine.

Das Frontend wird unabhängig vom Backend entwickelt, darf aber niemals eine eigene fachliche Wahrheit über das System etablieren.

Das Backend ist die autoritative Quelle für:

- Projekte
- Projektversionen
- Nodes
- Node Contracts
- Graphen
- Validierung
- Credentials
- Executions
- Execution States
- Events
- Logs
- Metrics
- Reports
- Plugins
- Benutzer und Berechtigungen

Das Frontend ist primär:

- Benutzeroberfläche
- Visualisierung
- Editor
- Konfigurationsoberfläche
- Orchestrierungsoberfläche
- Monitoring-Oberfläche
- Debugging-Oberfläche
- Reporting-Oberfläche

Die Frontend-Implementierung muss die in `/docs/vision.md` und `/docs/architecture.md` definierten Architekturprinzipien einhalten.

---

# 2. Zielarchitektur

Das Frontend soll als professionelle Webanwendung aufgebaut werden.

Empfohlener Stack:

- React
- TypeScript
- Vite
- Tailwind CSS
- React Router
- React Flow oder gleichwertige Graph-Editor-Technologie
- TanStack Query für Server State
- Zod oder gleichwertige Schema-/Runtime-Validierung
- WebSocket bzw. vergleichbare Echtzeitkommunikation
- Vitest
- React Testing Library
- Playwright
- ESLint
- Prettier
- TypeScript strict mode

Die konkrete Library darf angepasst werden, wenn eine bessere technische Entscheidung begründet werden kann.

Das Frontend muss insbesondere folgende Bereiche unterstützen:

```text
Application Shell
├── Dashboard
├── Projects
│   ├── Project List
│   ├── Project Details
│   ├── Project Versions
│   └── Project Editor
├── Node Library
├── Executions
│   ├── Execution List
│   ├── Execution Details
│   ├── Graph Execution View
│   └── Logs
├── Monitoring
├── Reports
├── Credentials
├── Plugins
└── Settings
```

----------

# 3. Grundprinzipien

## 3.1 Backend ist Source of Truth

Das Frontend darf keine Backend-Domänenlogik duplizieren, die dort bereits vorhanden ist.

Beispielsweise darf das Frontend nicht selbst entscheiden:

-   ob ein Graph valide ist
    
-   ob ein Node ausgeführt werden darf
    
-   ob ein Port kompatibel ist
    
-   ob ein Projekt veröffentlicht werden darf
    
-   welche Execution States gültig sind
    
-   ob Credentials verwendet werden dürfen
    

Das Frontend darf UI-seitige Vorvalidierung durchführen, aber Backend-Validierung bleibt autoritativ.

----------

## 3.2 Schema Driven UI

Node-Konfigurationen sollen möglichst aus den vom Backend gelieferten Node Contracts erzeugt werden.

Ein Node Contract kann beispielsweise enthalten:

```text
metadata
inputs
outputs
config_schema
capabilities
version
documentation
```

Das Frontend soll daraus dynamisch bzw. teilweise dynamisch erzeugen:

-   Node Darstellung
    
-   Input/Output Ports
    
-   Konfigurationsformular
    
-   Feldtypen
    
-   Required Fields
    
-   Defaults
    
-   Beschreibungen
    
-   Secret Fields
    
-   Validation Feedback
    
-   verfügbare Optionen
    

Hardcodierte Node-spezifische Formulare sollen vermieden werden.

----------

## 3.3 Server State und UI State trennen

Server State:

-   Projects
    
-   Nodes
    
-   Executions
    
-   Reports
    
-   Plugins
    
-   Credentials Metadata
    
-   Backend Validation Results
    

UI State:

-   aktuell selektierter Node
    
-   geöffnete Panels
    
-   Zoom
    
-   Editor Selection
    
-   Dialoge
    
-   Filter
    
-   lokale Draft-Eingaben
    
-   temporäre UI-Zustände
    

Diese beiden Kategorien dürfen nicht unnötig vermischt werden.

----------

## 3.4 Editor State

Der Graph Editor benötigt einen klar definierten Zustand:

```text
Project Draft
    ↓
Graph Editor State
    ↓
Backend Project Draft
    ↓
Validation
    ↓
Published Project Version
```

Der Editor darf nicht automatisch eine veröffentlichte Version verändern.

----------

## 3.5 Keine Business Logic in Komponenten

React Components sollen keine komplexe Geschäftslogik enthalten.

Bevorzugte Struktur:

```text
components/
features/
hooks/
services/
api/
state/
domain/
types/
utils/
```

Komponenten sollen primär:

-   darstellen
    
-   Benutzerinteraktionen entgegennehmen
    
-   Hooks verwenden
    
-   Events auslösen
    

----------

## 3.6 API Contracts

API-Aufrufe müssen zentralisiert werden.

Nicht:

```tsx
fetch("/api/v1/projects")
```

in beliebigen Komponenten.

Sondern beispielsweise:

```text
api/
  client.ts
  projects.ts
  nodes.ts
  executions.ts
  triggers.ts
  credentials.ts
  plugins.ts
  reports.ts
```

Die genaue Struktur darf angepasst werden.

----------

# 4. Entwicklungsregeln für OpenCode

Jede Phase dieser Roadmap ist als eigenständige OpenCode-Session gedacht.

OpenCode darf eine Phase erst beginnen, nachdem die vorherige Phase abgeschlossen wurde.

Für jede Session gilt:

1.  Repository untersuchen.
    
2.  `/AGENTS.md` lesen.
    
3.  `/frontend/AGENTS.md` lesen.
    
4.  `/docs/vision.md` lesen.
    
5.  `/docs/architecture.md` lesen.
    
6.  `/docs/roadmap-frontend.md` lesen.
    
7.  Aktuellen Frontend-Zustand analysieren.
    
8.  Abhängigkeiten zur Backend-API prüfen.
    
9.  Nur die aktuelle Phase implementieren.
    
10.  Bestehende Funktionalität nicht unnötig umbauen.
    
11.  Tests schreiben.
    
12.  Linter ausführen.
    
13.  Formatter ausführen.
    
14.  TypeScript prüfen.
    
15.  Build ausführen.
    
16.  Architektur auf Konsistenz prüfen.
    
17.  Dokumentation aktualisieren, falls erforderlich.
    
18.  Abschlussbericht erstellen.
    
19.  STOP.
    

OpenCode darf ausdrücklich nicht bereits zukünftige Phasen implementieren.

----------

# 5. Phasenübersicht

```text
Phase 00  Frontend Repository Foundation
Phase 01  Application Shell
Phase 02  Design System + UI Foundation
Phase 03  API Client + Backend Integration Foundation
Phase 04  Domain Types + Server State
Phase 05  Dashboard
Phase 06  Project Management
Phase 07  Project Draft + Graph Editor Foundation
Phase 08  Node Library
Phase 09  Node Configuration Inspector
Phase 10  Graph Editing + Connections
Phase 11  Graph Validation + Publish Workflow
Phase 12  Project Versions
Phase 13  Execution UI
Phase 14  Live Execution + WebSocket
Phase 15  Execution Inspector + Logs
Phase 16  Monitoring
Phase 17  Credentials UI
Phase 18  Triggers UI
Phase 19  Reports UI
Phase 20  Plugin Management UI
Phase 21  Settings + Authentication
Phase 22  Developer Experience
Phase 23  Frontend Security Hardening
Phase 24  Performance + UX Hardening
Phase 25  Responsive + Accessibility
Phase 26  E2E Testing
Phase 27  Frontend Production Readiness
Phase 28  Frontend V1 Integration
```

----------

# 6. Phase 00 — Frontend Repository Foundation

## Ziel

Eine saubere, professionelle Frontend-Grundlage schaffen.

## Scope

Einrichten:

-   React
    
-   TypeScript
    
-   Vite
    
-   Tailwind CSS
    
-   ESLint
    
-   Prettier
    
-   Vitest
    
-   React Testing Library
    
-   Playwright-Grundlage
    
-   TypeScript strict mode
    
-   Environment-Konfiguration
    
-   npm/Package-Manager-Struktur
    
-   grundlegende Verzeichnisstruktur
    

Beispiel:

```text
frontend/
├── src/
│   ├── app/
│   ├── components/
│   ├── features/
│   ├── pages/
│   ├── api/
│   ├── domain/
│   ├── hooks/
│   ├── state/
│   ├── utils/
│   └── main.tsx
├── tests/
├── public/
└── package.json
```

## Tests

-   TypeScript
    
-   Lint
    
-   Unit-Test
    
-   Build
    

## Acceptance Criteria

-   Frontend startet lokal.
    
-   Production Build funktioniert.
    
-   TypeScript strict ist aktiv.
    
-   Tests funktionieren.
    
-   Lint funktioniert.
    
-   Formatter funktioniert.
    
-   Keine unnötigen globalen Abhängigkeiten.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 00 aus docs/roadmap-frontend.md.

Lies zuerst:
- /AGENTS.md
- /frontend/AGENTS.md
- /docs/vision.md
- /docs/architecture.md
- /docs/roadmap-frontend.md

Analysiere den bestehenden Repository-Zustand.

Implementiere ausschließlich die Frontend Repository Foundation dieser Phase.

Richte React, TypeScript, Vite, Tailwind, ESLint, Prettier, Vitest, React Testing Library und die Playwright-Grundlage ein, soweit sie für diese Phase erforderlich sind.

Verwende TypeScript strict mode.

Erstelle eine saubere Frontend-Verzeichnisstruktur.

Keine Implementierung zukünftiger Features.

Führe Tests, Typecheck, Lint, Formatter und Production Build aus.

Behebe Probleme innerhalb dieser Phase.

Prüfe abschließend die Architektur.

Dokumentiere relevante Entscheidungen.

Erstelle einen kurzen Abschlussbericht:
- umgesetzt
- Tests
- offene Punkte
- bewusste Nicht-Implementierungen

STOP nach Abschluss von Phase 00.
```

----------

# 7. Phase 01 — Application Shell

## Ziel

Eine stabile globale Application Shell schaffen.

## Scope

Implementieren:

-   Router
    
-   Layout
    
-   Sidebar
    
-   Header
    
-   Navigation
    
-   Content Area
    
-   Error Boundary
    
-   Loading States
    
-   globale Fehlerbehandlung
    
-   grundlegende Navigation
    

Navigation:

```text
Dashboard
Projects
Executions
Monitoring
Reports
Credentials
Plugins
Settings
```

## Acceptance Criteria

-   Alle Hauptbereiche sind navigierbar.
    
-   URL-Routing funktioniert.
    
-   Layout ist konsistent.
    
-   Nicht vorhandene Routes zeigen eine sinnvolle 404-Seite.
    
-   Globale Fehler werden kontrolliert dargestellt.
    
-   Keine Business-Features werden vorweggenommen.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 01 aus docs/roadmap-frontend.md.

Lies zuerst:
- /AGENTS.md
- /frontend/AGENTS.md
- /docs/vision.md
- /docs/architecture.md
- /docs/roadmap-frontend.md

Analysiere Phase 00 und den bestehenden Frontend-Code.

Implementiere die Application Shell:
- Routing
- globale Layout-Struktur
- Sidebar
- Header
- Content Area
- Navigation
- Error Boundary
- globale Loading/Error States

Bereite die späteren Bereiche vor, implementiere deren Fachfunktionalität aber noch nicht.

Keine zukünftigen Features implementieren.

Führe Typecheck, Lint, Tests und Build aus.

STOP nach Phase 01.
```

----------

# 8. Phase 02 — Design System + UI Foundation

## Ziel

Eine konsistente UI-Basis schaffen.

## Scope

Implementieren:

-   Typography
    
-   Spacing
    
-   Buttons
    
-   Inputs
    
-   Selects
    
-   Checkbox
    
-   Switch
    
-   Tabs
    
-   Cards
    
-   Dialogs
    
-   Dropdowns
    
-   Tooltips
    
-   Badges
    
-   Alerts
    
-   Tables
    
-   Empty States
    
-   Loading Skeletons
    
-   Toasts
    
-   Confirmation Dialogs
    
-   Status Indicators
    

Ausführung über wiederverwendbare Komponenten.

## Acceptance Criteria

-   UI-Komponenten sind konsistent.
    
-   Keine unnötigen Inline-Styles.
    
-   Dark/Light Theme kann vorbereitet bzw. unterstützt werden.
    
-   Accessibility-Grundlagen sind vorhanden.
    
-   Komponenten sind isoliert testbar.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 02.

Lies die verpflichtenden Dokumente und analysiere den bestehenden Frontend-Code.

Baue eine konsistente UI Foundation und ein wiederverwendbares Design System.

Implementiere nur generische UI-Komponenten.

Keine fachlichen Project-, Node-, Execution- oder Backend-Features.

Schreibe Component Tests für relevante Komponenten.

Führe Typecheck, Lint, Tests und Build aus.

STOP nach Phase 02.
```

----------

# 9. Phase 03 — API Client + Backend Integration Foundation

## Ziel

Eine robuste zentrale Backend-Kommunikationsschicht schaffen.

## Scope

Implementieren:

-   HTTP Client
    
-   Base URL
    
-   Environment Configuration
    
-   Authentication Handling
    
-   Request/Response Handling
    
-   Error Normalization
    
-   API Types
    
-   Query Infrastructure
    
-   Mutation Infrastructure
    
-   Retry-Verhalten nur dort, wo sinnvoll
    
-   Request Cancellation
    

Beispiel:

```text
api/
├── client.ts
├── errors.ts
├── projects.ts
├── nodes.ts
├── executions.ts
├── triggers.ts
├── credentials.ts
├── plugins.ts
└── reports.ts
```

## Acceptance Criteria

-   Keine direkten Fetch-Aufrufe in Feature-Komponenten.
    
-   API-Fehler haben ein einheitliches Format.
    
-   API Layer ist testbar.
    
-   Environment-Konfiguration funktioniert.
    
-   Backend-Ausfall wird sauber dargestellt.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 03.

Analysiere die tatsächlich vorhandenen Backend-API-Contracts.

Implementiere eine zentrale API-Kommunikationsschicht.

Keine fachlichen UI-Features.

Vermeide direkte API-Aufrufe innerhalb von React-Komponenten.

Implementiere Tests für:
- erfolgreiche Requests
- API Errors
- Network Errors
- Cancellation
- Response Parsing

Nutze die tatsächlichen Backend-Verträge, soweit vorhanden.

Keine zukünftigen API-Endpunkte erfinden.

STOP nach Phase 03.
```

----------

# 10. Phase 04 — Domain Types + Server State

## Ziel

Eine typisierte Frontend-Repräsentation der Backend-Domäne schaffen.

## Scope

Types für:

-   Project
    
-   ProjectDraft
    
-   ProjectVersion
    
-   Graph
    
-   NodeInstance
    
-   Edge
    
-   NodeDefinition
    
-   NodePort
    
-   NodeConfigSchema
    
-   Trigger
    
-   Execution
    
-   NodeExecution
    
-   Event
    
-   Log
    
-   Metric
    
-   Report
    
-   Credential Metadata
    
-   Plugin
    

TanStack Query bzw. äquivalente Server-State-Lösung integrieren.

## Acceptance Criteria

-   Domain Types sind zentral definiert.
    
-   Server State wird nicht unnötig über lokale React States dupliziert.
    
-   Queries und Mutations sind strukturiert.
    
-   Cache Invalidation ist definiert.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 04.

Lies die Backend-Domänenmodelle und API-Verträge.

Implementiere typisierte Frontend-Domain-Modelle und Server-State-Infrastruktur.

Keine vollständigen Screens implementieren.

Keine zukünftigen fachlichen Funktionen.

Nutze Backend-Verträge als Source of Truth.

Führe Tests, Typecheck, Lint und Build aus.

STOP nach Phase 04.
```

----------

# 11. Phase 05 — Dashboard

## Ziel

Eine erste operative Übersicht über das System.

## Scope

Dashboard mit:

-   aktive Executions
    
-   letzte Executions
    
-   Erfolgs-/Fehlerübersicht
    
-   Laufzeiten
    
-   Token Usage
    
-   Kosten
    
-   letzte Fehler
    
-   System Status
    

## Acceptance Criteria

-   Dashboard verwendet echte Backend-Daten.
    
-   Loading, Empty und Error States existieren.
    
-   Daten können aktualisiert werden.
    
-   Keine erfundenen Statistiken.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 05.

Baue das Dashboard anhand der tatsächlich verfügbaren Backend-Daten.

Zeige:
- aktive Executions
- letzte Executions
- relevante Erfolgs-/Fehlerdaten
- Laufzeiten
- Token/Kosten sofern Backend diese liefert
- relevante Fehler

Keine Fake-Daten in Production-Code.

Implementiere Loading-, Empty- und Error-States.

STOP nach Phase 05.
```

----------

# 12. Phase 06 — Project Management

## Ziel

Projekte vollständig verwalten können.

## Scope

Implementieren:

-   Project List
    
-   Suche
    
-   Filter
    
-   Sortierung
    
-   Project Details
    
-   Create Project
    
-   Rename
    
-   Delete
    
-   Duplicate
    
-   Open Editor
    
-   Project Status
    

## Acceptance Criteria

Ein Benutzer kann:

```text
Project erstellen
→ öffnen
→ bearbeiten
→ speichern
→ löschen
```

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 06.

Implementiere die Project Management UI.

Unterstütze:
- Liste
- Suche
- Filter
- Sortierung
- Erstellen
- Öffnen
- Umbenennen
- Duplizieren
- Löschen

Nutze ausschließlich die vorhandenen Backend-APIs.

Noch keinen vollständigen Graph Editor implementieren.

STOP nach Phase 06.
```

----------

# 13. Phase 07 — Project Draft + Graph Editor Foundation

## Ziel

Den visuellen Projekt-Editor als technische Grundlage implementieren.

## Scope

Graph Editor mit:

-   Canvas
    
-   Pan
    
-   Zoom
    
-   Selection
    
-   Node Rendering
    
-   Edge Rendering
    
-   Minimap
    
-   Grid
    
-   Selection State
    
-   Undo/Redo Foundation
    
-   Copy/Paste Foundation
    
-   Auto Save Foundation
    

Noch keine vollständige Node Library.

## Acceptance Criteria

-   Graph kann dargestellt werden.
    
-   Nodes können ausgewählt werden.
    
-   Nodes können verschoben werden.
    
-   Edges können dargestellt werden.
    
-   Änderungen können als Draft behandelt werden.
    
-   Editor-State ist vom Published State getrennt.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 07.

Baue die technische Grundlage des visuellen Graph Editors.

Implementiere:
- Canvas
- Pan
- Zoom
- Node Selection
- Node Movement
- Edge Rendering
- Grid
- Minimap
- Editor State
- Undo/Redo Foundation
- Copy/Paste Foundation
- Draft Handling Foundation

Noch keine vollständige Node Library.
Noch keine komplexe Node-Konfiguration.
Noch kein Publish Workflow.

Die Trennung zwischen Project Draft und veröffentlichter Version muss erhalten bleiben.

STOP nach Phase 07.
```

----------

# 14. Phase 08 — Node Library

## Ziel

Eine professionelle Node Library bereitstellen.

## Scope

Node Library:

-   Suche
    
-   Kategorien
    
-   Filter
    
-   Node Preview
    
-   Beschreibung
    
-   Version
    
-   Capabilities
    
-   Drag & Drop
    
-   Add to Graph
    

Node Definitionen kommen vom Backend.

## Kategorien

Beispielsweise:

```text
Core
AI
Control Flow
HTTP
Web
Email
Data
Files
Triggers
Plugins
```

## Acceptance Criteria

-   Node Library verwendet Backend Node Registry.
    
-   Keine hardcodierte vollständige Node-Liste.
    
-   Nodes können in den Editor gezogen werden.
    
-   Node Metadata wird angezeigt.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 08.

Implementiere die Node Library auf Basis der Backend Node Registry.

Unterstütze:
- Suche
- Kategorien
- Filter
- Metadata
- Version
- Beschreibung
- Capabilities
- Drag & Drop in den Graph

Keine Node-spezifischen Konfigurationsformulare implementieren.

STOP nach Phase 08.
```

----------

# 15. Phase 09 — Node Configuration Inspector

## Ziel

Node-Konfiguration professionell bearbeiten können.

## Scope

Inspector Panel für ausgewählten Node.

Schema-driven:

```text
Node Contract
    ↓
Config Schema
    ↓
Form Renderer
    ↓
User Input
    ↓
Draft Config
```

Unterstützen:

-   String
    
-   Number
    
-   Boolean
    
-   Select
    
-   Secret Reference
    
-   JSON
    
-   List
    
-   Object
    
-   Textarea
    
-   Prompt Editor
    

## Acceptance Criteria

-   Formulare werden aus Schema generiert.
    
-   Required Fields werden berücksichtigt.
    
-   Defaults werden berücksichtigt.
    
-   Secret Values werden niemals angezeigt.
    
-   Backend Validation bleibt autoritativ.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 09.

Baue den Node Inspector schema-driven.

Verwende die Node Contracts des Backends.

Unterstütze die tatsächlich vorhandenen Schema-Typen.

Implementiere:
- dynamische Formulare
- Defaults
- Required Fields
- Descriptions
- Secret References
- Validation Feedback
- unsaved changes

Keine hardcodierten Formulare für einzelne Nodes, sofern dies durch das Backend-Schema vermeidbar ist.

STOP nach Phase 09.
```

----------

# 16. Phase 10 — Graph Editing + Connections

## Ziel

Vollständige Graph-Bearbeitung ermöglichen.

## Scope

Implementieren:

-   Nodes hinzufügen
    
-   Nodes löschen
    
-   Nodes duplizieren
    
-   Nodes verschieben
    
-   Edges erstellen
    
-   Edges löschen
    
-   Port Selection
    
-   Port Compatibility Feedback
    
-   Multi Selection
    
-   Group Selection
    
-   Keyboard Shortcuts
    
-   Copy/Paste
    
-   Undo/Redo
    
-   Auto Layout optional
    

## Acceptance Criteria

Ein Benutzer kann einen vollständigen Graph visuell erstellen.

Ungültige Verbindungen werden bereits UI-seitig möglichst früh verhindert bzw. markiert.

Backend bleibt autoritativ.

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 10.

Erweitere den Graph Editor zu einem vollständigen Graph Editing System.

Implementiere:
- Node Add/Delete/Duplicate
- Edge Add/Delete
- Port Selection
- Connection Feedback
- Multi Selection
- Keyboard Shortcuts
- Copy/Paste
- Undo/Redo

Nutze Node Contracts für Portinformationen.

Keine Execution-Funktionalität implementieren.

STOP nach Phase 10.
```

----------

# 17. Phase 11 — Graph Validation + Publish Workflow

## Ziel

Den vollständigen Draft → Validate → Publish Workflow abbilden.

## Scope

Implementieren:

```text
Draft
 ↓
Save
 ↓
Validate
 ↓
Validation Results
 ↓
Fix Errors
 ↓
Validate
 ↓
Publish
 ↓
Immutable Project Version
```

Validation UI:

-   Fehler
    
-   Warnings
    
-   Node
    
-   Port
    
-   Configuration
    
-   Graph
    
-   Navigation zum betroffenen Node
    

## Acceptance Criteria

-   Backend Validation wird dargestellt.
    
-   Fehler können angeklickt werden.
    
-   Editor navigiert zum betroffenen Node.
    
-   Publish ist nur bei gültigem Zustand möglich.
    
-   Published Version wird klar von Draft unterschieden.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 11.

Implementiere den vollständigen Draft-Validation-Publish Workflow.

Nutze die Backend-Validation als autoritative Quelle.

Zeige:
- Errors
- Warnings
- betroffene Nodes
- betroffene Ports
- Konfigurationsfehler

Ermögliche Navigation von Validation Error zum betroffenen Node.

Publish darf nicht als rein lokale Frontend-Entscheidung implementiert werden.

STOP nach Phase 11.
```

----------

# 18. Phase 12 — Project Versions

## Ziel

Versionen transparent verwalten.

## Scope

Implementieren:

-   Version List
    
-   Version Details
    
-   Published Version
    
-   Version Metadata
    
-   Created At
    
-   Author
    
-   Node/Graph Snapshot
    
-   Version Comparison Foundation
    
-   Restore/Clone als neuer Draft, sofern Backend unterstützt
    

## Acceptance Criteria

-   Versionen sind unveränderlich dargestellt.
    
-   Aktive Version ist erkennbar.
    
-   Draft und Published Version sind klar getrennt.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 12.

Implementiere die Project Version UI.

Zeige:
- Versionen
- Metadata
- aktive Version
- Erstellungsinformationen
- Version Details

Eine veröffentlichte Version darf im Frontend nicht als editierbarer Draft behandelt werden.

Implementiere Restore/Clone nur, wenn das Backend dies unterstützt.

STOP nach Phase 12.
```

----------

# 19. Phase 13 — Execution UI

## Ziel

Executions sichtbar und steuerbar machen.

## Scope

Execution List:

-   Status
    
-   Project
    
-   Project Version
    
-   Trigger
    
-   Start
    
-   Ende
    
-   Duration
    
-   Kosten
    
-   Tokens
    

Actions:

-   Run
    
-   Cancel
    
-   Open Details
    

## Execution States

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

## Acceptance Criteria

-   Status wird korrekt dargestellt.
    
-   Statusfarben sind nicht die einzige Informationsquelle.
    
-   Execution Details sind erreichbar.
    
-   Cancel wird nur angeboten, wenn Backend dies erlaubt.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 13.

Implementiere Execution List und grundlegende Execution Details.

Verwende ausschließlich die Backend Execution States.

Zeige:
- Project
- Version
- Trigger
- Status
- Start
- Ende
- Duration
- Tokens
- Kosten

Implementiere Run und Cancel nur entsprechend der Backend-API.

STOP nach Phase 13.
```

----------

# 20. Phase 14 — Live Execution + WebSocket

## Ziel

Laufende Executions in Echtzeit beobachten.

## Scope

Implementieren:

-   WebSocket Client
    
-   Connection Lifecycle
    
-   Reconnect
    
-   Event Handling
    
-   Execution Status Updates
    
-   Node Status Updates
    
-   Live Graph Updates
    
-   Connection Status Indicator
    

## Acceptance Criteria

Während einer laufenden Execution werden Änderungen ohne manuelles Refresh sichtbar.

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 14.

Implementiere die Echtzeitkommunikation für laufende Executions.

Unterstütze:
- WebSocket Connection
- Reconnect
- Connection State
- Execution Events
- Node State Updates
- Live Graph Status

Nutze die vom Backend definierten Event-Typen.

Keine eigene konkurrierende Event-Semantik erfinden.

STOP nach Phase 14.
```

----------

# 21. Phase 15 — Execution Inspector + Logs

## Ziel

Executions detailliert untersuchen können.

## Scope

Execution Inspector:

```text
Execution
├── Overview
├── Graph
├── Nodes
├── Logs
├── Errors
├── Events
├── Metrics
└── Cost
```

Node Inspector:

-   Status
    
-   Inputs
    
-   Outputs
    
-   Duration
    
-   Retry Count
    
-   Error
    
-   Logs
    
-   Token Usage
    
-   Cost
    

## Acceptance Criteria

Ein fehlgeschlagener Node kann nachvollziehbar untersucht werden.

Sensitive Daten werden entsprechend der Backend-Redaction nicht dargestellt.

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 15.

Baue den vollständigen Execution Inspector.

Unterstütze:
- Overview
- Execution Graph
- Node Details
- Logs
- Errors
- Events
- Metrics
- Token Usage
- Kosten

Node Execution Details müssen nachvollziehbar dargestellt werden.

Keine Secrets oder redigierte Daten rekonstruieren.

STOP nach Phase 15.
```

----------

# 22. Phase 16 — Monitoring

## Ziel

Systemweite Monitoring-Oberfläche.

## Scope

Dashboard für:

-   aktive Executions
    
-   Execution Rate
    
-   Success Rate
    
-   Failure Rate
    
-   Duration
    
-   Node Performance
    
-   Token Usage
    
-   Cost
    
-   Error Trends
    

Zeiträume:

```text
Last Hour
Last 24 Hours
Last 7 Days
Custom
```

## Acceptance Criteria

-   Metriken stammen aus Backend-Daten.
    
-   Zeitraumfilter funktionieren.
    
-   Empty States funktionieren.
    
-   Keine erfundenen Daten.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 16.

Implementiere die Monitoring-Oberfläche auf Basis der Backend-Metriken.

Zeige:
- Execution Rate
- Success/Failure
- Duration
- Node Performance
- Token Usage
- Kosten
- Fehler

Implementiere Zeitfilter nur für tatsächlich unterstützte Backend-Funktionen.

STOP nach Phase 16.
```

----------

# 23. Phase 17 — Credentials UI

## Ziel

Credentials sicher verwalten.

## Scope

Implementieren:

-   Credential List
    
-   Create
    
-   Update
    
-   Delete
    
-   Credential Type
    
-   Provider
    
-   Name
    
-   Metadata
    
-   Usage Information sofern verfügbar
    

Wichtig:

```text
Secret Value
    ↓
niemals wieder anzeigen
```

Nach Erstellung darf ein Secret nicht erneut als Klartext angezeigt werden, sofern Backend dies nicht explizit vorsieht.

## Acceptance Criteria

-   Keine Secret Values in UI State Logs.
    
-   Keine Secret Values in Browser Storage.
    
-   Keine Secret Values in URL.
    
-   Secret Fields werden maskiert.
    
-   Delete benötigt Bestätigung.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 17.

Implementiere die Credentials UI.

Security ist hierbei besonders wichtig.

Secrets dürfen nicht:
- in URLs erscheinen
- in localStorage gespeichert werden
- in Logs erscheinen
- in React Dev State unnötig persistiert werden
- in Fehlermeldungen erscheinen

Zeige nur Metadaten und maskierte Informationen.

STOP nach Phase 17.
```

----------

# 24. Phase 18 — Triggers UI

## Ziel

Trigger konfigurieren und verwalten.

## Scope

Unterstützen, soweit Backend vorhanden:

-   Manual
    
-   Cron
    
-   Interval
    
-   Webhook
    
-   API
    
-   Event
    

UI:

-   Trigger List
    
-   Create
    
-   Edit
    
-   Enable/Disable
    
-   Delete
    
-   Configuration
    
-   Status
    

## Acceptance Criteria

-   Trigger-Konfiguration verwendet Backend Contracts.
    
-   Aktivierung/Deaktivierung ist sichtbar.
    
-   Webhook-Informationen werden sicher dargestellt.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 18.

Implementiere die Trigger-Verwaltung.

Unterstütze nur Trigger-Typen, die vom Backend tatsächlich bereitgestellt werden.

Implementiere:
- Liste
- Erstellen
- Bearbeiten
- Aktivieren/Deaktivieren
- Löschen
- Configuration UI

Keine eigene Trigger-Logik im Frontend.

STOP nach Phase 18.
```

----------

# 25. Phase 19 — Reports UI

## Ziel

Ausführungsberichte anzeigen und exportieren.

## Scope

Implementieren:

-   Report List
    
-   Report Details
    
-   Execution Summary
    
-   Duration
    
-   Nodes
    
-   Errors
    
-   Tokens
    
-   Costs
    
-   Export
    
-   Download
    

## Acceptance Criteria

Reports basieren ausschließlich auf Backend-Reports.

Sensitive Daten werden nicht clientseitig aus redigierten Daten rekonstruiert.

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 19.

Implementiere die Reports UI.

Zeige Report-Daten aus dem Backend.

Unterstütze:
- Liste
- Details
- Execution Summary
- Fehler
- Nodes
- Tokens
- Kosten
- Export/Download

Keine eigene Report-Berechnung als Ersatz für das Backend.

STOP nach Phase 19.
```

----------

# 26. Phase 20 — Plugin Management UI

## Ziel

Plugins verwalten.

## Scope

Implementieren:

-   Installed Plugins
    
-   Plugin Details
    
-   Version
    
-   Compatibility
    
-   Dependencies
    
-   Enabled/Disabled
    
-   Install
    
-   Remove
    
-   Validation Errors
    

Kein Marketplace.

## Acceptance Criteria

-   Plugin Lifecycle wird korrekt dargestellt.
    
-   Kompatibilitätsprobleme werden verständlich angezeigt.
    
-   Keine Plugin-Installation ohne Backend-Validierung.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 20.

Implementiere die Plugin Management UI.

Unterstütze:
- Installed Plugins
- Metadata
- Version
- Dependencies
- Compatibility
- Enable/Disable
- Install
- Remove

Kein Marketplace.

Keine eigene Plugin-Validierungslogik als Ersatz für das Backend.

STOP nach Phase 20.
```

----------

# 27. Phase 21 — Settings + Authentication

## Ziel

Globale Einstellungen und Benutzerzugang.

## Scope

Implementieren:

-   Login
    
-   Logout
    
-   Session State
    
-   Current User
    
-   Settings
    
-   Theme
    
-   API/Backend Status
    
-   Account Information
    
-   Security-relevante UI
    

Keine Enterprise SSO-Oberfläche.

## Acceptance Criteria

-   Nicht authentifizierte Nutzer können geschützte Bereiche nicht öffnen.
    
-   Session-Verlust wird sauber behandelt.
    
-   Logout funktioniert.
    
-   Auth-State ist zentral.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 21.

Implementiere Authentication und Settings UI entsprechend den tatsächlich vorhandenen Backend-APIs.

Unterstütze:
- Login
- Logout
- Session
- Current User
- Protected Routes
- Settings

Keine Enterprise SSO-Funktionalität.

STOP nach Phase 21.
```

----------

# 28. Phase 22 — Developer Experience

## Ziel

Das Frontend für Entwickler und Power User verbessern.

## Scope

Implementieren:

-   Keyboard Shortcuts
    
-   Command Palette
    
-   bessere Graph Navigation
    
-   Node Search
    
-   Quick Add
    
-   Copy/Paste
    
-   Debug Information
    
-   API/Backend Diagnostics
    
-   Shortcuts Help
    
-   Editor Performance Tools
    

## Acceptance Criteria

Der Editor lässt sich effizient mit Maus und Tastatur bedienen.

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 22.

Verbessere die Developer Experience.

Priorität:
- Keyboard Shortcuts
- Command Palette
- Quick Node Search
- Quick Add
- Graph Navigation
- Copy/Paste
- Shortcut Help
- Diagnostics

Keine neuen Backend-Fachfunktionen implementieren.

STOP nach Phase 22.
```

----------

# 29. Phase 23 — Frontend Security Hardening

## Ziel

Das Frontend sicher gegen typische Web-Anwendungsprobleme machen.

## Scope

Prüfen und härten:

-   XSS
    
-   unsafe HTML
    
-   Token Handling
    
-   Secret Handling
    
-   URL Handling
    
-   Open Redirects
    
-   WebSocket Authentication
    
-   Error Disclosure
    
-   Sensitive Browser Storage
    
-   CSP soweit sinnvoll
    
-   Dependency Security
    
-   File Upload UI
    
-   Download Handling
    

## Acceptance Criteria

-   Keine unnötige Speicherung sensibler Daten.
    
-   Keine unsichere HTML-Ausgabe.
    
-   Security relevante Fehler werden nicht unnötig offengelegt.
    
-   Dependency Audit durchgeführt.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 23.

Führe ein systematisches Frontend Security Hardening durch.

Prüfe insbesondere:
- XSS
- unsafe HTML
- Auth Tokens
- Secrets
- Browser Storage
- URL Handling
- Open Redirects
- WebSockets
- Error Disclosure
- File Uploads
- Downloads
- Dependencies

Behebe konkrete Probleme.

Keine zukünftigen Features implementieren.

Führe Security-relevante Tests und vollständige Validierung aus.

STOP nach Phase 23.
```

----------

# 30. Phase 24 — Performance + UX Hardening

## Ziel

Frontend für reale Nutzung optimieren.

## Scope

Prüfen:

-   Bundle Size
    
-   Code Splitting
    
-   Lazy Loading
    
-   Graph Rendering
    
-   Large Graph Performance
    
-   Execution List Pagination
    
-   Virtualized Lists
    
-   Query Caching
    
-   unnecessary Re-Renders
    
-   WebSocket Event Handling
    
-   Debouncing
    
-   Autosave
    
-   Loading States
    

## Acceptance Criteria

-   Große Graphen bleiben nutzbar.
    
-   Lange Execution Lists bleiben performant.
    
-   Keine unnötigen API Requests.
    
-   Editor reagiert flüssig.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 24.

Analysiere das Frontend hinsichtlich Performance und UX.

Optimiere nur nach messbaren bzw. nachvollziehbaren Problemen.

Prüfe:
- Bundle
- Code Splitting
- Lazy Loading
- Graph Rendering
- Large Graphs
- Lists
- Query Caching
- Re-Renders
- WebSocket Events
- Autosave

Keine vorzeitige komplexe Optimierung.

STOP nach Phase 24.
```

----------

# 31. Phase 25 — Responsive + Accessibility

## Ziel

Professionelle Bedienbarkeit auf unterschiedlichen Bildschirmgrößen und für unterschiedliche Nutzer.

## Scope

Accessibility:

-   Keyboard Navigation
    
-   Focus Management
    
-   ARIA
    
-   Labels
    
-   Dialog Accessibility
    
-   Screen Reader Support
    
-   Contrast
    
-   Reduced Motion
    
-   Error Announcements
    

Responsive:

-   Desktop
    
-   Laptop
    
-   kleinere Screens
    

Der Graph Editor darf desktop-first sein, muss aber bei kleineren Viewports sinnvoll reagieren.

## Acceptance Criteria

-   Hauptfunktionen sind per Tastatur erreichbar.
    
-   Dialoge sind zugänglich.
    
-   Formulare haben Labels.
    
-   Fehler werden zugänglich kommuniziert.
    
-   Responsive Layout bricht nicht.
    

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 25.

Führe einen Accessibility- und Responsive-Pass durch.

Prüfe:
- Keyboard Navigation
- Focus
- ARIA
- Labels
- Dialoge
- Forms
- Error Messages
- Contrast
- Reduced Motion
- Responsive Layout

Behebe konkrete Probleme.

STOP nach Phase 25.
```

----------

# 32. Phase 26 — E2E Testing

## Ziel

Kritische Benutzerabläufe automatisiert absichern.

## Scope

Playwright E2E Tests für:

### Project Flow

```text
Login
→ Project erstellen
→ Project öffnen
→ Node hinzufügen
→ Node konfigurieren
→ Graph speichern
→ Validate
→ Publish
```

### Execution Flow

```text
Project öffnen
→ Run
→ Execution öffnen
→ Live Status
→ Logs
→ Ergebnis
```

### Import/Export Flow

```text
Export
→ Import
→ Project öffnen
→ Validate
→ Run
```

### Credential Flow

```text
Credential erstellen
→ Node referenziert Credential
→ Secret bleibt verborgen
```

## Acceptance Criteria

Die wichtigsten V1 Workflows funktionieren automatisiert.

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 26.

Baue robuste Playwright E2E Tests für die wichtigsten V1 Workflows.

Mindestens:
- Login
- Project Creation
- Node Creation
- Node Configuration
- Graph Save
- Validation
- Publish
- Execution
- Live Execution
- Logs
- Import/Export
- Credentials

Verwende echte Backend-Testumgebung bzw. definierte Test Fixtures.

Keine Fake-Assertions, die nur das UI testen ohne die Funktionalität zu verifizieren.

STOP nach Phase 26.
```

----------

# 33. Phase 27 — Frontend Production Readiness

## Ziel

Das Frontend für produktive Self-hosted Deployments vorbereiten.

## Scope

Prüfen:

-   Production Build
    
-   Environment Configuration
    
-   API Base URL
    
-   WebSocket URL
    
-   Reverse Proxy Compatibility
    
-   Error Pages
    
-   Loading States
    
-   Offline/Backend Unavailable State
    
-   Cache
    
-   Asset Handling
    
-   Browser Compatibility
    
-   Deployment Documentation
    

## Acceptance Criteria

Ein Production Build kann gegen das produktive Backend betrieben werden.

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 27.

Führe einen vollständigen Production Readiness Pass durch.

Prüfe:
- Production Build
- Environment Configuration
- API URLs
- WebSocket URLs
- Reverse Proxy
- Error Handling
- Backend Unavailable
- Asset Handling
- Browser Compatibility

Dokumentiere relevante Deployment-Anforderungen.

STOP nach Phase 27.
```

----------

# 34. Phase 28 — Frontend V1 Integration

## Ziel

Das gesamte Frontend als zusammenhängendes Produkt validieren.

## Scope

End-to-End Prüfung:

```text
Login
 ↓
Dashboard
 ↓
Project
 ↓
Project Draft
 ↓
Node Library
 ↓
Graph Editor
 ↓
Node Configuration
 ↓
Validation
 ↓
Publish
 ↓
Project Version
 ↓
Trigger
 ↓
Execution
 ↓
Live Execution
 ↓
Execution Inspector
 ↓
Logs
 ↓
Metrics
 ↓
Report
 ↓
Export
 ↓
Import
 ↓
erneute Execution
```

Zusätzlich:

-   Plugins
    
-   Credentials
    
-   Settings
    
-   Monitoring
    
-   Error States
    
-   Empty States
    
-   Permission Errors
    
-   Backend Errors
    
-   WebSocket Disconnect
    
-   Session Expiration
    

## Acceptance Criteria

Der vollständige V1 User Journey funktioniert.

Es dürfen keine grundlegenden Frontend-Blocker mehr vorhanden sein.

## OpenCode Prompt

```text
Implementiere ausschließlich Phase 28 aus docs/roadmap-frontend.md.

Dies ist die finale Frontend V1 Integration.

Lies:
- /AGENTS.md
- /frontend/AGENTS.md
- /docs/vision.md
- /docs/architecture.md
- /docs/roadmap-frontend.md
- /docs/roadmap-backend.md

Prüfe den vollständigen User Journey:

Login
→ Dashboard
→ Project
→ Draft
→ Node Library
→ Graph Editor
→ Node Configuration
→ Validation
→ Publish
→ Project Version
→ Trigger
→ Execution
→ Live Execution
→ Execution Inspector
→ Logs
→ Metrics
→ Report
→ Export
→ Import
→ erneute Execution

Zusätzlich:
- Credentials
- Plugins
- Monitoring
- Settings
- Error States
- Empty States
- Auth Errors
- Backend Errors
- WebSocket Disconnect
- Session Expiration

Führe sämtliche relevanten Unit-, Integration- und E2E-Tests aus.

Führe außerdem aus:
- Typecheck
- Lint
- Formatter
- Production Build

Behebe nur echte Integrations- und V1-Probleme.

Keine neuen Features außerhalb des V1 Scopes hinzufügen.

Prüfe die Frontend-Architektur gegen:
- /docs/vision.md
- /docs/architecture.md

Erstelle abschließend einen V1 Completion Report mit:
- vollständig umgesetzt
- teilweise umgesetzt
- bekannte Einschränkungen
- technische Schulden
- Tests
- Build Status

STOP nach Abschluss von Phase 28.
```

----------

# 35. Abhängigkeitsgraph

Die Phasen werden grundsätzlich sequenziell abgearbeitet:

```text
00
 ↓
01
 ↓
02
 ↓
03
 ↓
04
 ↓
05
 ↓
06
 ↓
07
 ↓
08
 ↓
09
 ↓
10
 ↓
11
 ↓
12
 ↓
13
 ↓
14
 ↓
15
 ↓
16
 ↓
17
 ↓
18
 ↓
19
 ↓
20
 ↓
21
 ↓
22
 ↓
23
 ↓
24
 ↓
25
 ↓
26
 ↓
27
 ↓
28
```

Einzelne technische Arbeiten können innerhalb einer Phase parallelisiert werden, die Phasen selbst sollen jedoch als abgeschlossene Meilensteine behandelt werden.

----------

# 36. Session-Protokoll

Jede OpenCode-Session soll genau einem Meilenstein entsprechen.

## Start

OpenCode bekommt den Prompt der jeweiligen Phase.

## Analyse

OpenCode:

1.  liest die verpflichtenden Dokumente
    
2.  untersucht den aktuellen Code
    
3.  prüft bestehende Implementierungen
    
4.  prüft Backend-Verträge
    
5.  identifiziert notwendige Änderungen
    

## Implementierung

OpenCode implementiert ausschließlich die aktuelle Phase.

## Qualitätssicherung

Mindestens:

```text
Typecheck
Lint
Format
Unit Tests
Build
```

Je nach Phase zusätzlich:

```text
Integration Tests
E2E Tests
Security Tests
```

## Architektur Review

Vor Abschluss muss geprüft werden:

```text
Passt die Implementierung zur Vision?
Passt sie zur Architektur?
Wurde Backend-Logik unnötig dupliziert?
Wurde zukünftiger Scope vorgezogen?
Sind Abhängigkeiten sauber?
Sind Komponenten wiederverwendbar?
```

## Abschluss

OpenCode meldet:

```text
Phase:
Status:

Implementiert:
- ...

Tests:
- ...

Typecheck:
- ...

Lint:
- ...

Build:
- ...

Offene Punkte:
- ...

Bewusst nicht implementiert:
- ...

Technische Entscheidungen:
- ...
```

Danach:

```text
STOP
```

----------

# 37. Definition of Done pro Phase

Eine Phase gilt nur als abgeschlossen, wenn:

-   Scope implementiert
    
-   keine unbeabsichtigten zukünftigen Features enthalten
    
-   bestehende Features weiterhin funktionieren
    
-   TypeScript erfolgreich
    
-   Lint erfolgreich
    
-   Formatter erfolgreich
    
-   relevante Tests vorhanden
    
-   relevante Tests erfolgreich
    
-   Build erfolgreich
    
-   API-Verträge eingehalten
    
-   Architektur eingehalten
    
-   keine unnötige technische Schuld eingeführt
    
-   Sicherheitsanforderungen der Phase erfüllt
    
-   Abschlussbericht erstellt
    

----------

# 38. Frontend V1 Acceptance Criteria

Das Frontend V1 gilt als abgeschlossen, wenn ein Benutzer mindestens folgenden Ablauf vollständig durchführen kann:

```text
Login
  ↓
Dashboard
  ↓
Project erstellen
  ↓
Project öffnen
  ↓
Project Draft bearbeiten
  ↓
Node aus Node Library auswählen
  ↓
Node in Graph ziehen
  ↓
Node konfigurieren
  ↓
Nodes verbinden
  ↓
Graph validieren
  ↓
Validation Errors beheben
  ↓
Project publishen
  ↓
Project Version erhalten
  ↓
Execution starten
  ↓
Live Execution beobachten
  ↓
Node Status beobachten
  ↓
Execution Inspector öffnen
  ↓
Logs untersuchen
  ↓
Metrics untersuchen
  ↓
Kosten/Token Usage untersuchen
  ↓
Report öffnen
  ↓
Project exportieren
  ↓
Project importieren
  ↓
importiertes Project validieren
  ↓
importiertes Project erneut ausführen
```

Zusätzlich müssen folgende Bereiche funktionieren:

```text
Credentials
Plugins
Triggers
Monitoring
Settings
Authentication
Error Handling
```

----------

# 39. V1 Frontend Qualitätsanforderungen

## Architektur

-   TypeScript strict
    
-   klare Feature-Struktur
    
-   API Layer zentralisiert
    
-   Server State sauber getrennt
    
-   UI State sauber getrennt
    
-   keine unnötige Business Logic in Components
    
-   Backend bleibt Source of Truth
    

## Editor

-   Graph Editing
    
-   Node Library
    
-   Node Inspector
    
-   Connections
    
-   Validation
    
-   Undo/Redo
    
-   Copy/Paste
    
-   Draft/Published Separation
    
-   Performance bei größeren Graphen
    

## Execution

-   Execution List
    
-   Execution Details
    
-   Live Status
    
-   Node Status
    
-   Logs
    
-   Errors
    
-   Metrics
    
-   Costs
    
-   Token Usage
    

## Sicherheit

-   Secrets nicht im Frontend persistieren
    
-   keine Secrets in Logs
    
-   keine Secrets in URLs
    
-   XSS geprüft
    
-   Auth geprüft
    
-   WebSocket Auth geprüft
    
-   Error Disclosure geprüft
    
-   Dependency Audit
    

## UX

-   Loading States
    
-   Empty States
    
-   Error States
    
-   Confirmation Dialogs
    
-   Keyboard Navigation
    
-   Accessibility
    
-   Responsive Layout
    
-   konsistentes Design System
    

## Qualität

-   Unit Tests
    
-   Integration Tests
    
-   E2E Tests
    
-   Typecheck
    
-   Lint
    
-   Production Build
    

----------

# 40. Frontend V1 Completion Model

Das Frontend soll am Ende folgenden Weg vollständig abbilden:

```text
Backend Node Registry
        ↓
Node Library
        ↓
User selects Node
        ↓
Graph Editor
        ↓
Node Inspector
        ↓
Project Draft
        ↓
Backend Validation
        ↓
Publish
        ↓
Project Version
        ↓
Trigger
        ↓
Execution
        ↓
WebSocket Events
        ↓
Live Execution Graph
        ↓
Execution Inspector
        ↓
Logs + Metrics
        ↓
Report
```

Für Entwickler:

```text
Python Node
    ↓
Node SDK
    ↓
Plugin
    ↓
Plugin Registry
    ↓
Backend Node Registry
    ↓
Frontend Node Library
    ↓
Graph Editor
    ↓
Project
    ↓
Execution Runtime
```

Das Frontend ist damit nicht nur eine CRUD-Oberfläche, sondern die visuelle Entwicklungs-, Orchestrierungs-, Monitoring- und Debugging-Oberfläche der gesamten Agent Orchestration Engine.