# AI Agent Engine

> **Godot for AI Agents.**
>
> Eine selbst gehostete visuelle Entwicklungs- und Orchestrierungsplattform zum Erstellen, Ausführen, Überwachen und Versionieren modularer KI-Systeme.

---

## 1. Was ist dieses Projekt?

Dieses Projekt entwickelt eine **AI Agent Engine** – eine allgemeine Entwicklungs- und Laufzeitplattform für modulare KI-Systeme.

Die zentrale Idee lässt sich vereinfacht als

> **„Godot für AI Agents“**

beschreiben.

Die Analogie zu Godot ist bewusst gewählt:

Godot ist nicht einfach ein Tool zum Erstellen eines einzelnen Spiels. Es ist eine **Engine und Entwicklungsumgebung**, in der Entwickler Projekte aus wiederverwendbaren Bausteinen aufbauen, konfigurieren, testen, ausführen, debuggen und erweitern können.

Unsere Plattform verfolgt dasselbe Prinzip für **KI-Systeme**.

Benutzer sollen nicht für jeden Anwendungsfall eine neue individuelle AI-Anwendung programmieren müssen.

Stattdessen erstellen sie ein **Project**, bauen darin einen **Graphen aus Nodes**, konfigurieren diese Nodes und lassen die Engine diesen Graphen ausführen.

Beispiel:

```text
Webhook
   │
   ▼
Input
   │
   ▼
LLM
   │
   ▼
If
 ┌─┴──────────────┐
 │                │
 ▼                ▼
Web Search       SMTP
 │                │
 ▼                ▼
LLM             Output
 └───────┬────────┘
         ▼
       Output
```

Der Benutzer beschreibt damit einen Prozess.

Die Engine kümmert sich um die technische Ausführung:

-   Graphvalidierung
    
-   Abhängigkeiten
    
-   Scheduling
    
-   Node-Ausführung
    
-   Datenweitergabe
    
-   Parallelität
    
-   Fehlerbehandlung
    
-   Retries
    
-   Timeouts
    
-   Cancellation
    
-   Persistenz
    
-   Events
    
-   Logs
    
-   Metrics
    
-   Credentials
    
-   Versionierung
    
-   Trigger
    
-   Reports
    
-   Recovery
    

----------

# 2. Was bauen wir genau?

Wir bauen **keinen einzelnen AI-Agenten**.

Wir bauen eine **Engine, auf der viele unterschiedliche AI-Systeme erstellt werden können**.

Das zentrale Produkt besteht aus drei Ebenen:

```text
┌─────────────────────────────────────────────────────────┐
│                    FRONTEND / IDE                       │
│                                                         │
│  Projects · Graph Editor · Node Library · Inspector    │
│  Executions · Logs · Monitoring · Reports · Settings   │
└────────────────────────────┬────────────────────────────┘
                             │
                             │ API / WebSocket
                             ▼
┌─────────────────────────────────────────────────────────┐
│                    AI AGENT ENGINE                      │
│                                                         │
│  Project System                                         │
│  Graph System                                           │
│  Validation                                             │
│  Execution Runtime                                      │
│  Scheduler                                              │
│  Trigger System                                         │
│  Event System                                           │
│  Observability                                          │
│  Credentials                                            │
│  Versioning                                              │
│  Plugin System                                          │
│  AI Provider Abstraction                                │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────┐
│                    INFRASTRUCTURE                       │
│                                                         │
│  PostgreSQL · Redis · File/Object Storage               │
│  AI Providers · HTTP · SMTP · Web Search · etc.        │
└─────────────────────────────────────────────────────────┘
```

Die **Engine ist der Kern des Produkts**.

Das Frontend ist die Entwicklungs- und Bedienoberfläche für diese Engine.

----------

# 3. Das wichtigste mentale Modell

Wer an diesem Repository arbeitet, sollte immer dieses Modell im Kopf behalten:

```text
Engine
  │
  ├── Projects
  │      │
  │      ├── Draft
  │      │    └── Graph
  │      │         ├── Nodes
  │      │         └── Edges
  │      │
  │      └── Published Versions
  │
  ├── Node Registry
  │      ├── Built-in Nodes
  │      ├── Python Nodes
  │      └── Plugins
  │
  ├── Triggers
  │
  ├── Execution Runtime
  │      ├── Scheduler
  │      ├── Dependency Resolution
  │      ├── Node Execution
  │      ├── Retry
  │      ├── Timeout
  │      └── Cancellation
  │
  ├── Events
  ├── Logs
  ├── Metrics
  ├── Credentials
  └── Reports
```

Die zentrale Frage bei jeder Implementierung lautet:

> **An welcher Stelle der Engine gehört diese Funktion fachlich hin?**

Nicht:

> „In welche Datei kann ich diesen Code schnell einbauen?“

----------

# 4. Core Concepts

## 4.1 Engine

Die Engine ist die zentrale Runtime des Systems.

Sie kennt:

-   Projects
    
-   Graphen
    
-   Nodes
    
-   Edges
    
-   Triggers
    
-   Executions
    
-   Node Executions
    
-   Events
    
-   Credentials
    
-   Plugins
    
-   Observability
    

Die Engine ist für die korrekte Ausführung verantwortlich.

----------

## 4.2 Project

Ein Project beschreibt ein eigenständiges AI-System.

Beispielsweise:

```text
Customer Support Automation
```

oder:

```text
Invoice Processing
```

oder:

```text
Research Assistant
```

Ein Project besitzt unter anderem:

-   Metadaten
    
-   Graph
    
-   Node-Konfigurationen
    
-   Prompts/Templates
    
-   Trigger
    
-   Ressourcen
    
-   Abhängigkeiten
    

----------

## 4.3 Project Draft

Ein Draft ist die **veränderbare Arbeitsversion** eines Projects.

Benutzer können:

-   Nodes hinzufügen
    
-   Nodes entfernen
    
-   Nodes konfigurieren
    
-   Verbindungen ändern
    
-   Prompts ändern
    
-   Trigger konfigurieren
    

Der Draft ist mutable.

----------

## 4.4 Project Version

Ein veröffentlichter Project-Stand ist **immutable**.

Beispiel:

```text
Project
└── Versions
    ├── v1
    ├── v2
    ├── v3
    └── v4
```

Eine Execution läuft immer gegen einen konkreten, unveränderlichen Versionsstand.

Damit ist nachvollziehbar:

> Welche Version des Systems wurde tatsächlich ausgeführt?

Das ist eine fundamentale Architekturentscheidung.

----------

# 5. Graph

Ein Project besteht aus einem Graphen.

Der Graph enthält:

-   Nodes
    
-   Node Instances
    
-   Edges
    
-   Ports
    
-   Konfigurationen
    

Beispiel:

```text
[Webhook]
     │
     ▼
[LLM]
     │
     ▼
[If]
   /   \
  /     \
 ▼       ▼
[HTTP] [SMTP]
```

Der Graph beschreibt den Prozess.

Die Execution Runtime interpretiert diesen Graphen und führt ihn aus.

----------

# 6. Nodes

Nodes sind die grundlegenden Bausteine der Plattform.

Ein Node besitzt einen definierten Contract.

Vereinfacht:

```text
Node
├── Identity
├── Metadata
├── Version
├── Configuration Schema
├── Inputs
├── Outputs
├── Validation
├── Capabilities
└── Runtime Behavior
```

Beispiele:

```text
core.input
core.output
core.log
core.if
core.switch
core.delay
core.merge
core.set
core.pick
core.http
core.web_search
core.smtp
core.llm
```

Weitere Nodes können durch Python oder Plugins hinzugefügt werden.

----------

# 7. Ein LLM ist nur ein Node

Eine wichtige Architekturentscheidung:

> **LLM ist nicht die Engine.**

Ein LLM ist eine Funktion innerhalb eines größeren Systems.

Beispielsweise:

```text
Input
  ↓
LLM
  ↓
If
  ├── Web Search
  │      ↓
  │     LLM
  │
  └── SMTP
```

Die Engine bleibt provider- und use-case-unabhängig.

Ein LLM Node verwendet eine AI-Abstraktion:

```text
LLM Node
    ↓
AI Service
    ↓
LLM Provider Abstraction
    ↓
Provider Adapter
    ↓
External Provider
```

Dadurch wird verhindert, dass die gesamte Engine an einen einzelnen AI-Anbieter gekoppelt wird.

----------

# 8. Agent

Ein Agent ist **keine fundamentale Engine-Abstraktion**.

Ein Agent ist ein spezialisierter Node oder ein entsprechender Subgraph.

Beispielsweise:

```text
Research Agent
│
├── LLM
├── Web Search
├── Decision Logic
├── Tool Calls
└── Output
```

Dadurch bleibt die Engine allgemeingültig.

Wir bauen keine Engine, die nur einen bestimmten „Agent-Typ“ kennt.

Wir bauen eine Engine, mit der solche Systeme modelliert werden können.

----------

# 9. Subgraphs

Komplexe Graphen können in kleinere wiederverwendbare Einheiten aufgeteilt werden.

Beispielsweise:

```text
Main Workflow
│
├── Validate Input
│
├── Research Subgraph
│   ├── Search
│   ├── Filter
│   └── Summarize
│
├── Decision
│
└── Notification
```

Das ermöglicht modulare und wiederverwendbare AI-Systeme.

----------

# 10. Execution Runtime

Die Execution Runtime ist einer der wichtigsten Teile des Systems.

Sie erhält einen veröffentlichten Graphen und führt ihn aus.

Vereinfacht:

```text
Trigger
   ↓
Execution
   ↓
Load Project Version
   ↓
Validate
   ↓
Build Execution Plan
   ↓
Schedule Nodes
   ↓
Execute Nodes
   ↓
Propagate Outputs
   ↓
Handle Branches / Parallelism
   ↓
Persist State
   ↓
Emit Events
   ↓
Complete / Fail / Cancel
```

Die Runtime muss unter anderem unterstützen:

-   lineare Abläufe
    
-   Branching
    
-   Conditions
    
-   Switches
    
-   Loops
    
-   Foreach
    
-   Parallelität
    
-   Merge
    
-   Delay
    
-   Retry
    
-   Timeout
    
-   Cancellation
    
-   Fehlerbehandlung
    
-   State Persistence
    

----------

# 11. Trigger

Ein Project wird nicht einfach nur über einen globalen „Play“-Button ausgeführt.

Executions entstehen durch Trigger.

Beispiele:

```text
Manual
API
Webhook
Cron
Interval
Event
```

Alle Trigger führen letztendlich in denselben Execution-Mechanismus.

```text
Cron ────────┐
Webhook ────┤
API ────────┤
Manual ─────┤
Event ──────┘
             ↓
      Execution Service
             ↓
      Execution Runtime
```

Dadurch gibt es nicht mehrere unterschiedliche Execution-Systeme.

----------

# 12. Events, Logs und Metrics

Die Plattform muss nachvollziehbar machen, was während einer Execution passiert.

Beispielsweise:

```text
Execution Started
      ↓
Node A Started
      ↓
Node A Completed
      ↓
Node B Started
      ↓
Node B Failed
      ↓
Retry Scheduled
      ↓
Node B Started
      ↓
Node B Completed
      ↓
Execution Completed
```

Dazu gehören:

-   strukturierte Logs
    
-   Events
    
-   Metrics
    
-   Timings
    
-   Fehler
    
-   Correlation IDs
    
-   Token Usage
    
-   Kosteninformationen, soweit verfügbar
    

Events sind dabei **nicht der kanonische Zustand**.

Der persistierte Domain-Zustand bleibt maßgeblich.

----------

# 13. Credentials und Secrets

Secrets dürfen niemals Bestandteil normaler Project-Konfigurationen sein.

Beispielsweise darf ein Project nicht enthalten:

```json
{
  "api_key": "sk-..."
}
```

Stattdessen:

```text
Node Configuration
        ↓
Credential Reference
        ↓
Credential Store
        ↓
Secret Resolution
        ↓
Runtime
```

Secrets dürfen nicht versehentlich in:

-   Logs
    
-   Events
    
-   Reports
    
-   API Responses
    
-   Project Exports
    
-   Fehlermeldungen
    

auftauchen.

----------

# 14. Plugin-System

Die Plattform soll erweiterbar sein.

Dafür existiert ein Plugin-System.

Plugins können beispielsweise bereitstellen:

```text
Plugin
├── Nodes
├── Providers
├── Integrations
├── Resources
└── Metadata
```

Das System soll dadurch langfristig erweiterbar sein, ohne den Engine-Core für jede neue Funktion verändern zu müssen.

Ein Plugin ist jedoch nicht einfach beliebiger Code ohne Contract.

Plugins benötigen:

-   Manifest
    
-   Version
    
-   Dependencies
    
-   Compatibility Information
    
-   Registration
    
-   Validation
    
-   Lifecycle
    
-   Documentation
    

----------

# 15. Python SDK

Entwickler sollen eigene Nodes schreiben können.

Beispielsweise:

```python
class MyNode(Node):
    ...
```

Das SDK stellt dafür definierte Schnittstellen bereit:

-   Node Metadata
    
-   Inputs
    
-   Outputs
    
-   Configuration
    
-   Execution Context
    
-   Logging
    
-   Metrics
    
-   Credentials
    
-   Events
    
-   Errors
    

Custom Nodes sollen nicht direkt interne Engine-Implementierungsdetails manipulieren.

Das SDK bildet die stabile Entwicklergrenze.

----------

# 16. Backend

Das Backend ist die **autoritative Quelle der Wahrheit**.

Das Frontend entscheidet nicht über:

-   Project Validität
    
-   Graph Validität
    
-   Execution State
    
-   Credentials
    
-   Berechtigungen
    
-   Versionierung
    
-   Geschäftsregeln
    

Diese Regeln liegen im Backend.

Die grundlegende Architektur:

```text
backend/
├── api/
├── application/
├── domain/
├── infrastructure/
├── runtime/
├── nodes/
├── providers/
├── plugins/
├── security/
└── observability/
```

Die genaue Struktur und Verantwortung ist in:

```text
/backend/AGENTS.md
/docs/architecture.md
/docs/roadmap-backend.md
```

definiert.

----------

# 17. Frontend

Das Frontend ist die visuelle Entwicklungsumgebung und Monitoring-Oberfläche.

Es stellt unter anderem bereit:

-   Dashboard
    
-   Project Management
    
-   Graph Editor
    
-   Node Library
    
-   Node Inspector
    
-   Validation
    
-   Publish Workflow
    
-   Version Management
    
-   Execution Monitoring
    
-   Execution Inspector
    
-   Logs
    
-   Metrics
    
-   Reports
    
-   Credentials
    
-   Triggers
    
-   Plugins
    
-   Settings
    

Technologie:

```text
React
TypeScript
Vite
Tailwind
React Router
TanStack Query
React Flow
WebSocket
```

Das Frontend ist **nicht** die Business-Logic-Schicht.

Es visualisiert und bearbeitet den Zustand der Engine.

----------

# 18. Backend und Frontend sind bewusst getrennt

Die Kommunikation erfolgt über klar definierte Contracts.

```text
Frontend
   │
   │ HTTP / WebSocket
   ▼
Backend API
   │
   ▼
Application Services
   │
   ▼
Domain / Runtime
```

Es gibt keine direkte Kopplung zwischen UI-Komponenten und Datenbank.

Es gibt keinen direkten Datenbankzugriff aus dem Frontend.

Es gibt keine verstreuten `fetch()`-Aufrufe in beliebigen React-Komponenten.

----------

# 19. Versionierung und Reproduzierbarkeit

Eine Execution muss nachvollziehbar sein.

Mindestens relevant sind:

```text
Project Version
Node Version
Plugin Version
Configuration Snapshot
Trigger
Engine Version
Runtime Information
Execution Metadata
```

Beispiel:

```text
Execution #1842

Project:      Research Assistant
Project:      v7
Node:         ai.llm@1.3.0
Plugin:       web-search@2.1.0
Engine:       0.8.0
Trigger:      webhook
Started:      ...
Finished:     ...
```

AI-Ausgaben selbst können aufgrund nichtdeterministischer Modelle trotzdem variieren.

Reproduzierbarkeit bedeutet daher:

> Der technische Ausgangszustand einer Execution ist nachvollziehbar und versioniert.

Nicht:

> Eine AI liefert unter allen Umständen exakt denselben Text.

----------

# 20. Was wir ausdrücklich NICHT bauen

Die folgenden Dinge gehören bewusst nicht zum V1-Scope:

-   Multi-Tenant SaaS
    
-   Billing
    
-   Marketplace
    
-   Mobile App
    
-   Kubernetes Operator
    
-   verteiltes Cluster
    
-   Enterprise SSO
    
-   komplexes Enterprise-RBAC
    
-   komplexe Agent Memory Plattform
    
-   eigene Vector Database Layer
    
-   Browser Automation
    
-   Multi-Agent Collaboration Framework
    

Das bedeutet nicht, dass diese Dinge niemals möglich sind.

Sie sind lediglich **nicht Bestandteil von V1**.

Die Architektur soll zukünftige Erweiterungen nicht unnötig verhindern.

----------

# 21. Technologische Grundprinzipien

## Backend

Primär:

```text
Python
uv
FastAPI
Pydantic
SQLAlchemy
Alembic
PostgreSQL
Redis
```

Weitere Technologien dürfen verwendet werden, wenn sie architektonisch sinnvoll sind.

----------

## Frontend

Primär:

```text
React
TypeScript
Vite
Tailwind
React Router
TanStack Query
React Flow
WebSocket
Vitest
React Testing Library
Playwright
```

----------

# 22. Qualitätsanspruch

Dieses Repository wird wie ein **Production-Softwareprojekt** behandelt.

Nicht wie ein Prototyp, Demo-Projekt oder Hackathon-Projekt.

Dabei gelten insbesondere:

-   klare Architektur
    
-   starke Typisierung
    
-   kleine Verantwortlichkeiten
    
-   explizite Domain-Modelle
    
-   reproduzierbare Builds
    
-   automatisierte Tests
    
-   echte Integrations-Tests
    
-   Migrationstests
    
-   API-Tests
    
-   Contract-Tests
    
-   Security-Tests
    
-   E2E-Tests
    
-   Observability
    
-   strukturierte Fehler
    
-   sichere Secret-Behandlung
    
-   saubere Dokumentation
    
-   keine unnötige technische Verschuldung
    

----------

# 23. Keine Fake-Tests

Tests müssen reale Eigenschaften des Systems überprüfen.

Nicht:

```python
assert True
```

Nicht:

```python
mock_everything()
```

nur damit ein Test grün wird.

Für Infrastrukturtests sollen nach Möglichkeit echte Testinfrastrukturen verwendet werden.

Beispielsweise:

```text
PostgreSQL
Redis
HTTP Test Service
SMTP Test Service
```

über reproduzierbare Testumgebungen wie Testcontainers.

Tests müssen isoliert sein und sich selbst vorbereiten und aufräumen.

Kein manuelles:

> „Starte vorher lokal PostgreSQL.“

----------

# 24. Architektur vor Convenience

Wenn eine schnelle Implementierung der Architektur widerspricht, gewinnt die Architektur.

Beispielsweise ist es nicht akzeptabel, eine Business-Regel einfach in einen React-Component zu verschieben, nur weil es dort einfacher ist.

Ebenso ist es nicht akzeptabel, den Execution Runtime State irgendwo in einer API-Funktion zu verstecken, nur weil das für einen ersten Prototyp schneller wäre.

Wir bauen zuerst die richtigen Abstraktionen und implementieren darauf.

----------

# 25. Source of Truth

Das Backend ist die autoritative Quelle für persistente und fachliche Zustände.

Beispiele:

```text
Project State        → Backend
Project Version      → Backend
Graph Validation     → Backend
Execution State      → Backend
Node State           → Backend
Credentials          → Backend
Authorization        → Backend
```

Das Frontend darf optimistisch arbeiten, muss aber bei Konflikten und nach Reconnect immer gegen den autoritativen Backend-Zustand reconciliieren.

----------

# 26. Repository-Struktur

Die geplante Struktur:

```text
/
├── AGENTS.md
├── README.md
├── .gitignore
├── .env.example
├── docker-compose.yml
│
├── backend/
│   ├── AGENTS.md
│   ├── README.md
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── src/
│   └── tests/
│
├── frontend/
│   ├── AGENTS.md
│   ├── README.md
│   ├── package.json
│   ├── package-lock.json / pnpm-lock.yaml
│   ├── src/
│   └── tests/
│
└── docs/
    ├── vision.md
    ├── architecture.md
    ├── roadmap-backend.md
    └── roadmap-frontend.md
```

----------

# 27. Die wichtigsten Dokumente

## `/AGENTS.md`

Globale Engineering-Regeln.

Definiert unter anderem:

-   Architekturprinzipien
    
-   Coding Standards
    
-   Testing
    
-   Security
    
-   Quality Gates
    
-   Entwicklungsregeln
    

----------

## `/backend/AGENTS.md`

Backend-spezifische Regeln.

Enthält unter anderem:

-   Python Architecture
    
-   Domain/Application/API
    
-   Execution Runtime
    
-   Nodes
    
-   Plugins
    
-   AI Provider
    
-   Persistence
    
-   Security
    
-   Testing
    

----------

## `/frontend/AGENTS.md`

Frontend-spezifische Regeln.

Enthält unter anderem:

-   React Architecture
    
-   State Management
    
-   Graph Editor
    
-   API Integration
    
-   WebSocket
    
-   Security
    
-   Accessibility
    
-   Testing
    

----------

## `/docs/vision.md`

Beschreibt:

> Warum existiert das Produkt?

Dort steht das langfristige Produktverständnis.

----------

## `/docs/architecture.md`

Beschreibt:

> Wie ist das System technisch aufgebaut und warum?

----------

## `/docs/roadmap-backend.md`

Definiert die Backend-Entwicklung in sequenziellen Phasen.

----------

## `/docs/roadmap-frontend.md`

Definiert die Frontend-Entwicklung in sequenziellen Phasen.

----------

# 28. Entwicklungsprozess

Die Entwicklung erfolgt **phasenweise**.

Eine Phase entspricht grundsätzlich einer eigenständigen OpenCode-Session.

Beispiel Backend:

```text
Phase 00
Repository Foundation
        ↓
Phase 01
Backend Application Skeleton
        ↓
Phase 02
Core Domain Model
        ↓
Phase 03
Node Contract
        ↓
...
        ↓
Phase 27
Backend V1 Integration
```

Danach:

```text
Frontend Phase 00
        ↓
Frontend Phase 01
        ↓
...
        ↓
Frontend Phase 28
```

Die vollständige Reihenfolge steht in den jeweiligen Roadmaps.

----------

# 29. Strict Phase Isolation

Ein Entwickler oder AI Coding Agent darf nicht einfach mehrere Roadmap-Phasen gleichzeitig implementieren.

Wenn beispielsweise Phase 07 bearbeitet wird:

```text
DO:
- Phase 07 vollständig implementieren
- Tests schreiben
- Architektur prüfen
- Dokumentation aktualisieren
- Quality Gates ausführen

DO NOT:
- Phase 08 vorwegnehmen
- Phase 09 vorbereiten
- zukünftige Features „schon einmal“ einbauen
- Scope eigenmächtig erweitern
```

Das verhindert, dass sich Architektur und Entwicklungsstand vermischen.

----------

# 30. Jede Phase endet mit Quality Gates

Eine Phase gilt erst als abgeschlossen, wenn:

```text
Implementation
      ↓
Tests
      ↓
Formatter
      ↓
Linter
      ↓
Type Checker
      ↓
Integration Tests
      ↓
Architecture Review
      ↓
Documentation Review
      ↓
Acceptance Criteria
      ↓
Completion Report
```

erfüllt sind.

Wenn Tests fehlschlagen, ist die Phase nicht abgeschlossen.

----------

# 31. Entwicklung mit OpenCode

OpenCode erhält pro Phase einen expliziten Arbeitsauftrag.

Der Agent soll vor Beginn immer lesen:

```text
/AGENTS.md
/backend/AGENTS.md
/docs/vision.md
/docs/architecture.md
/docs/roadmap-backend.md
```

bzw. für Frontend:

```text
/AGENTS.md
/frontend/AGENTS.md
/docs/vision.md
/docs/architecture.md
/docs/roadmap-frontend.md
```

Danach:

1.  Repository untersuchen
    
2.  aktuellen Implementierungsstand verstehen
    
3.  aktuelle Phase identifizieren
    
4.  Scope der Phase verstehen
    
5.  bestehende Architektur respektieren
    
6.  implementieren
    
7.  Tests schreiben/ausführen
    
8.  Quality Gates ausführen
    
9.  Dokumentation aktualisieren, falls erforderlich
    
10.  Acceptance Criteria prüfen
    
11.  Completion Report erstellen
    
12.  **STOP**
    

----------

# 32. Wenn OpenCode unterbrochen wird

Eine unterbrochene Session bedeutet nicht, dass die Arbeit neu begonnen werden soll.

Bei einer Fortsetzung muss zuerst der aktuelle Repository-Zustand untersucht werden:

```text
- Git Status
- bestehende Änderungen
- bereits angelegte Dateien
- TODOs
- Tests
- Fehlermeldungen
- aktuelle Phase
- Acceptance Criteria
```

Danach wird exakt dort weitergearbeitet, wo die Implementierung tatsächlich steht.

Es darf nicht blind von vorne begonnen werden.

Insbesondere dürfen keine bereits implementierten Teile dupliziert oder überschrieben werden.

----------

# 33. Backend zuerst

Die Entwicklung beginnt mit dem Backend.

Grund:

> **Das Backend definiert die fachlichen Contracts und ist die autoritative Quelle der Wahrheit.**

Die Reihenfolge ist deshalb grundsätzlich:

```text
Backend
   ↓
Backend V1
   ↓
Frontend
   ↓
Frontend V1
   ↓
Full Integration
```

Das bedeutet nicht, dass Frontend-Entwicklung technisch unmöglich wäre, bevor das Backend vollständig fertig ist.

Es bedeutet, dass die **fachlichen Contracts zuerst stabilisiert werden**, bevor sich das Frontend auf eine instabile Business-Logik stützt.

----------

# 34. Das Zielbild

Am Ende soll ein Benutzer ungefähr folgenden Prozess durchlaufen können:

```text
Login
  ↓
Dashboard
  ↓
Create Project
  ↓
Open Project
  ↓
Create Draft
  ↓
Open Graph Editor
  ↓
Add Nodes
  ↓
Configure Nodes
  ↓
Connect Nodes
  ↓
Validate Graph
  ↓
Publish Version
  ↓
Configure Trigger
  ↓
Trigger Execution
  ↓
Execution Runtime
  ↓
Live Execution
  ↓
Execution Inspector
  ↓
Logs / Metrics
  ↓
Report
  ↓
Export Project
  ↓
Import Project
  ↓
Execute Imported Project
```

----------

# 35. Beispiel: Ein vollständiges AI-System

Ein Benutzer möchte eingehende Kundenanfragen automatisch bearbeiten.

Er baut:

```text
Webhook
   │
   ▼
Input
   │
   ▼
LLM
   │
   ├── classify intent
   │
   ▼
Switch
 ┌─┼─────────────┐
 │ │             │
 ▼ ▼             ▼
FAQ Search   Web Search   Human Review
 │              │             │
 └──────┬───────┘             │
        ▼                     │
       LLM                    │
        │                     │
        └──────────┬──────────┘
                   ▼
                 SMTP
                   │
                   ▼
                 Output
```

Die Plattform stellt dafür bereit:

```text
Graph Editor
Node Library
Node Inspector
Validation
LLM Provider
Web Search
SMTP
Execution Runtime
Retries
Timeouts
Logs
Metrics
Credentials
Reports
Versioning
```

Der konkrete Kundenprozess ist **nur ein Project**.

Die Engine selbst bleibt allgemein.

----------

# 36. Die wichtigste Abgrenzung

Dieses Projekt ist **nicht**:

```text
❌ nur ein Chatbot
❌ nur ein Prompt Builder
❌ nur eine LangChain UI
❌ nur ein Workflow Editor
❌ nur ein LLM Playground
❌ nur ein Agent Builder
❌ nur ein API Wrapper
```

Es ist:

```text
┌───────────────────────────────────────────────┐
│             AI DEVELOPMENT ENGINE             │
│                                               │
│  Projects                                     │
│  Graphs                                       │
│  Nodes                                        │
│  Plugins                                      │
│  Triggers                                     │
│  Execution Runtime                            │
│  State                                        │
│  Persistence                                  │
│  Observability                                │
│  Credentials                                  │
│  Versioning                                   │
│  Extensibility                                │
└───────────────────────────────────────────────┘
```

Die treffendste Kurzbeschreibung lautet:

> **Godot für AI Agents: eine selbst gehostete Engine und visuelle Entwicklungsumgebung für modulare, versionierbare und ausführbare KI-Systeme.**

----------

# 37. Engineering North Star

Bei jeder Änderung soll folgende Kette erfüllt sein:

```text
Verstanden
    ↓
Architektonisch eingeordnet
    ↓
Implementiert
    ↓
Validiert
    ↓
Getestet
    ↓
Beobachtbar
    ↓
Gesichert
    ↓
Dokumentiert
    ↓
Reproduzierbar
```

Wenn eine Änderung nur „funktioniert“, aber nicht nachvollziehbar getestet, beobachtbar, sicher und architektonisch eingeordnet ist, ist sie nicht fertig.

----------

# 38. Kurzfassung für neue Entwickler

Wenn du dieses Repository zum ersten Mal öffnest, merke dir fünf Dinge:

### 1. Wir bauen eine Engine

Nicht nur eine UI und nicht nur einen Agenten.

### 2. Nodes sind die grundlegenden Bausteine

LLMs, HTTP, Web Search, SMTP, Control Flow usw. werden als Nodes modelliert.

### 3. Projects bestehen aus Graphen

Der Graph beschreibt das AI-System.

### 4. Die Runtime führt veröffentlichte Versionen aus

Drafts sind veränderbar, Published Versions unveränderlich.

### 5. Das Backend ist die Wahrheit

Das Frontend visualisiert und steuert die Engine, entscheidet aber nicht über deren fachlichen Zustand.

----------

## Final Product Definition

> **This project is a self-hosted AI development and execution engine, conceptually similar to Godot for game development. It allows users to visually build modular AI systems from reusable nodes, connect them into executable graphs, configure triggers and credentials, publish immutable project versions, execute them through a reliable runtime, and inspect their behavior through logs, events, metrics and reports. Developers can extend the platform with custom Python nodes and plugins.**
> 
> **The goal is not to build one AI agent. The goal is to build the engine on which many different AI agents, workflows and automation systems can be created.**