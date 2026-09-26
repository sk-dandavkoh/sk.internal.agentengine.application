# AI Agent Orchestration Engine — Vision

## 1. Purpose

Die AI Agent Orchestration Engine ist eine professionelle, self-hosted Plattform zur Entwicklung, Orchestrierung, Ausführung, Überwachung und Verwaltung modularer AI-Agent-Systeme.

Die Plattform stellt eine zentrale Engine bereit, auf der Benutzer eigene AI-Anwendungen als Projects erstellen können.

Ein Project besteht aus standardisierten Nodes, die über einen visuellen Graph miteinander verbunden werden. Nodes können AI-Modelle, Agents, Tools, Datenquellen, externe APIs, Kontrolllogik, Kommunikationskanäle oder eigene Python-Implementierungen repräsentieren.

Die Engine übernimmt dabei die Infrastruktur und Runtime:

- Project-Verwaltung
- Graph-Modellierung
- Node Registry
- Node-Ausführung
- Trigger und Scheduling
- Event-Verarbeitung
- Fehlerbehandlung
- Retry und Timeout
- Credentials und Secrets
- Logging
- Monitoring
- Metrics
- Kosten- und Token-Tracking
- Reporting
- Project-Versionierung
- Import und Export
- Plugin-System
- API
- WebSocket-basierte Live-Events
- Developer SDK
- Self-hosted Deployment

Die zentrale Idee ist:

> Die Engine betreibt Agent-Systeme. Projects definieren Agent-Systeme. Nodes bilden die funktionalen Bausteine. Executions sind konkrete Ausführungen eines Project-Zustands.

---

# 2. Product Vision

Die Plattform soll die Entwicklung von AI-Agent-Systemen ähnlich vereinfachen, wie eine klassische Game Engine die Entwicklung von Spielen vereinfacht.

Die Engine stellt eine standardisierte technische Umgebung bereit.

Benutzer müssen für ein neues Agentensystem nicht jedes Mal:

- eigene Orchestrierung bauen,
- eigene Trigger implementieren,
- eigene Logging-Infrastruktur entwickeln,
- eigene Execution-Verwaltung erstellen,
- eigene Monitoring-Lösungen entwickeln,
- eigene LLM-Abstraktionen implementieren,
- eigene Retry-Mechanismen schreiben,
- eigene Project-Konfigurationen definieren.

Stattdessen sollen sie vorhandene Nodes kombinieren oder bei Bedarf eigene Nodes entwickeln.

Die Plattform soll sowohl für technische Entwickler als auch für technisch weniger erfahrene Benutzer geeignet sein.

Technische Benutzer sollen mit Python eigene Nodes und komplexe Agentensysteme entwickeln können.

Nicht-technische Benutzer sollen vorhandene Nodes über den visuellen Editor kombinieren und konfigurieren können.

---

# 3. Core Concept

Die Plattform basiert auf vier zentralen Konzepten:

1. Engine
2. Project
3. Node
4. Execution

## 3.1 Engine

Die Engine ist die dauerhaft laufende Anwendung.

Sie stellt bereit:

- Runtime
- API
- Project Management
- Node Registry
- Plugin System
- Scheduler
- Trigger System
- Event System
- Credential Management
- Observability
- Reporting
- Persistence
- WebSocket Events

Die Engine ist nicht an ein einzelnes Project gebunden.

Eine Installation kann gleichzeitig viele unterschiedliche Projects verwalten und ausführen.

---

## 3.2 Project

Ein Project ist eine in sich logisch gekapselte Agent-Anwendung.

Ein Project beschreibt ein konkretes Problem oder einen konkreten Geschäftsprozess.

Beispiele:

- Lead-Generierung
- Lead-Qualifizierung
- Dokumentenanalyse
- Kundenservice
- E-Mail-Verarbeitung
- Marktanalyse
- Datenaufbereitung
- interne Recherche
- Content-Erstellung
- Reporting
- automatisierte Backoffice-Prozesse

Ein Project besitzt einen Graph aus Nodes und Connections.

Projects werden durch die Engine ausgeführt.

Ein Project muss in v1 nicht unabhängig von der Engine deploybar sein.

Die Engine ist die Runtime für Projects.

---

## 3.3 Node

Ein Node ist eine standardisierte ausführbare Einheit.

Ein Node kann beispielsweise sein:

- LLM
- AI Agent
- Web Search
- HTTP Request
- SMTP
- Email
- Database
- File Reader
- JSON Parser
- Conditional Logic
- Loop
- Parallel Execution
- Logger
- Transformer
- Human Approval
- Custom Python Code

Nodes besitzen definierte:

- Inputs
- Outputs
- Configuration
- Metadata
- Validation
- Runtime-Verhalten
- Version
- Dokumentation

Nodes müssen dem Node Contract der Engine entsprechen.

---

## 3.4 Execution

Eine Execution ist eine konkrete Ausführung eines Project-Version-Snapshots.

Eine Execution besitzt unter anderem:

- eindeutige ID
- Project-ID
- Project-Version
- Trigger
- Status
- Startzeit
- Endzeit
- Dauer
- Fehlerinformationen
- Node Executions
- Logs
- Metrics
- Tokenverbrauch
- Kosteninformationen

Eine Execution ist unabhängig von späteren Änderungen am Project.

---

# 4. Godot-Inspiration

Die Architektur orientiert sich konzeptionell an der Idee einer Engine wie Godot, ohne Godot technisch nachzubauen.

Die Analogie lautet:

| Game Engine | AI Agent Engine |
|---|---|
| Engine | Agent Engine |
| Project | Agent Project |
| Scene | Graph |
| Node | Node |
| Script | Python Node |
| Resource | Resource |
| Inspector | Node Inspector |
| Signals | Events |
| Run | Execution |
| Editor | Visual Agent Editor |
| Plugin | Node Plugin |
| Export | Project Package |
| Runtime | Execution Runtime |

Diese Analogie dient als Architekturmodell.

Nicht benötigte Bestandteile klassischer Game Engines werden nicht übernommen.

Insbesondere benötigt die Plattform keine:

- 3D Engine
- Rendering Engine
- Physics Engine
- Animation Engine
- Audio Engine
- Game-specific Asset Pipeline

---

# 5. Fundamental Architecture Principles

## 5.1 Engine First

Die Engine ist das eigentliche Produkt.

Projects sind Artefakte, die auf der Engine laufen.

Die Runtime darf nicht projektspezifisch implementiert werden.

---

## 5.2 Project / Runtime Separation

Projects beschreiben, was ausgeführt werden soll.

Die Runtime entscheidet, wie es ausgeführt wird.

Projects dürfen keine direkte Kontrolle über interne Runtime-Mechanismen erhalten.

---

## 5.3 Node Contract First

Nodes sind nur dann Teil der Engine, wenn sie den definierten Node Contract erfüllen.

Die Engine darf nicht davon ausgehen, dass jeder Node intern gleich implementiert ist.

Ein Node muss über standardisierte Metadaten, Ports, Configuration und Execution Contracts verfügen.

---

## 5.4 Graph First

Agentensysteme werden als Graphen modelliert.

Ein Graph besteht aus:

- Nodes
- Ports
- Connections
- Configuration
- optionalen Subgraphs

Die Graphstruktur ist unabhängig vom Frontend.

Der visuelle Editor ist lediglich eine Benutzeroberfläche für dieses Graph-Modell.

---

## 5.5 Execution Snapshot

Eine Execution darf niemals von späteren Änderungen am Project beeinflusst werden.

Vor dem Start einer Execution wird ein konsistenter Project-Version-Snapshot bestimmt.

Die Execution arbeitet ausschließlich gegen diesen Snapshot.

---

## 5.6 Provider Agnostic

Die Engine darf nicht von einem einzelnen AI Provider abhängig sein.

LLM-Funktionalität muss über eine Abstraktionsschicht bereitgestellt werden.

Unterstützt werden sollen unter anderem:

- OpenAI
- Anthropic
- Ollama
- vLLM
- Azure OpenAI
- weitere Provider

LiteLLM kann als Provider-Abstraktion verwendet werden.

Die Engine-Architektur darf jedoch nicht von LiteLLM als fundamentaler Domain-Abstraktion abhängig werden.

---

## 5.7 Backend as Source of Truth

Das Backend ist autoritative Quelle für:

- Projects
- Graphs
- Nodes
- Configuration
- Validation
- Executions
- Triggers
- Credentials
- Reports
- Runtime State

Das Frontend darf keine eigene konkurrierende Domain-Implementierung besitzen.

---

## 5.8 Schema Driven UI

Node-Konfigurationen sollen möglichst aus maschinenlesbaren Schemas erzeugt werden.

Das Backend stellt Node Metadata und Configuration Schemas bereit.

Das Frontend kann daraus automatisch:

- Formulare
- Inputs
- Selects
- Sliders
- Validation
- Beschreibungen

erzeugen.

Neue Nodes sollen möglichst nicht zwingend eine eigene Frontend-Komponente benötigen.

---

## 5.9 API Driven

Frontend, CLI und externe Integrationen verwenden die gleichen Application Services über definierte APIs.

Geschäftslogik darf nicht ausschließlich im Frontend implementiert werden.

---

## 5.10 Observable by Default

Executions und Nodes müssen von Anfang an beobachtbar sein.

Logging, Metrics, Execution Events, Fehler und Laufzeiten sind Bestandteil der Runtime und kein nachträgliches Add-on.

---

# 6. User Types

Die Plattform richtet sich primär an zwei Benutzergruppen.

## 6.1 Developer

Ein Developer soll:

- eigene Nodes entwickeln,
- Python verwenden,
- eigene Integrationen erstellen,
- komplexe Graphen bauen,
- Nodes parametrisieren,
- Plugins installieren,
- Projects exportieren,
- Projects versionieren,
- die Engine über API und CLI bedienen können.

Der Developer benötigt keine Einschränkung auf den visuellen Editor.

---

## 6.2 Non-Technical User

Ein Non-Technical User soll:

- Projects erstellen,
- Nodes aus der Library auswählen,
- Nodes per Drag & Drop platzieren,
- Nodes verbinden,
- Konfigurationen über Formulare setzen,
- Projects validieren,
- Projects veröffentlichen,
- Executions starten,
- Monitoring verwenden,
- Reports betrachten

können.

Der Benutzer soll nicht zwingend Python schreiben müssen.

---

# 7. Project Lifecycle

Projects durchlaufen einen definierten Lifecycle.

```text
Draft
  ↓
Validated
  ↓
Published
  ↓
Active
  ↓
Disabled
  ↓
Archived
```

## Draft

Project befindet sich in Bearbeitung.

## Validated

Project erfüllt die technischen Validierungsregeln.

## Published

Eine konkrete Project-Version wurde veröffentlicht.

## Active

Die Project-Version kann durch Trigger ausgeführt werden.

## Disabled

Automatische Trigger sind deaktiviert.

## Archived

Project wird nicht mehr aktiv verwendet.

----------

# 8. Draft and Published Separation

Der Editor arbeitet mit einem Draft.

Eine laufende Runtime darf nicht direkt den aktuellen Draft verwenden.

Stattdessen:

```text
Draft
  ↓
Validation
  ↓
Publish
  ↓
Project Version
  ↓
Trigger
  ↓
Execution Snapshot
```

Dadurch kann ein Benutzer ein Project verändern, ohne laufende oder zukünftige Produktionsausführungen unbeabsichtigt zu verändern.

----------

# 9. Node Categories

Die Engine soll eine Standardbibliothek von Nodes bereitstellen.

## Core

-   Input
    
-   Output
    
-   Set
    
-   Transform
    
-   Template
    
-   JSON Parse
    
-   JSON Serialize
    
-   Logger
    

## AI

-   LLM
    
-   Structured Output
    
-   Embedding
    
-   Agent
    

## Web

-   HTTP Request
    
-   Web Search
    
-   Webhook
    

## Communication

-   Email
    
-   SMTP
    

## Data

-   File Read
    
-   File Write
    
-   CSV
    
-   JSON
    
-   Database
    

## Control Flow

-   If
    
-   Switch
    
-   ForEach
    
-   Loop
    
-   Parallel
    
-   Merge
    
-   Delay
    
-   Retry
    
-   Try
    
-   Catch
    

## Trigger

-   Manual
    
-   Cron
    
-   Interval
    
-   Webhook
    
-   Event
    
-   API
    

Die Node Library soll langfristig beliebig erweiterbar sein.

----------

# 10. Agents

Ein Agent ist keine fundamentale Engine-Abstraktion.

Die fundamentalen Abstraktionen sind:

```text
Node
Graph
Execution
```

Ein Agent ist ein spezialisierter Node bzw. ein spezialisierter Graph-Mechanismus.

Dadurch bleibt die Engine nicht auf eine bestimmte Definition von „Agent“ beschränkt.

Ein Agent kann beispielsweise enthalten:

-   Instructions
    
-   LLM
    
-   Tools
    
-   Context
    
-   Iterationslimit
    
-   Termination Rules
    
-   optional Memory
    

----------

# 11. Subgraphs

Projects sollen hierarchische Graphen unterstützen können.

Ein Node kann intern einen Subgraph repräsentieren.

Beispiel:

```text
Lead Qualification
```

kann intern enthalten:

```text
Input
  ↓
LLM
  ↓
Score
  ↓
Decision
  ↓
Output
```

Der Parent Graph sieht lediglich den Subgraph Node.

Damit können komplexe Agentensysteme modular aufgebaut werden.

----------

# 12. Control Flow

Die Runtime muss neben linearen Graphen auch komplexe Ausführungen ermöglichen.

Unterstützt werden sollen:

-   Bedingungen
    
-   Verzweigungen
    
-   Schleifen
    
-   Iterationen
    
-   Parallelität
    
-   Merges
    
-   Delays
    
-   Retries
    
-   Fehlerpfade
    
-   Subgraphs
    

Damit sollen auch komplexe Geschäftsprozesse ohne individuellen Runtime-Code modellierbar sein.

----------

# 13. Triggers

Projects können durch unterschiedliche Mechanismen gestartet werden.

Mindestens:

-   manuell
    
-   Cron
    
-   Interval
    
-   Webhook
    
-   Event
    
-   API
    

Ein Trigger erzeugt eine Execution Request.

```text
Trigger
  ↓
Execution Request
  ↓
Execution Queue
  ↓
Execution Runtime
```

----------

# 14. Event System

Die Engine besitzt ein internes Event-System.

Events können beispielsweise sein:

-   project.created
    
-   project.updated
    
-   project.published
    
-   execution.created
    
-   execution.started
    
-   execution.paused
    
-   execution.resumed
    
-   execution.completed
    
-   execution.failed
    
-   execution.cancelled
    
-   node.started
    
-   node.completed
    
-   node.failed
    
-   trigger.fired
    

Events dienen unter anderem als Grundlage für:

-   Monitoring
    
-   Trigger
    
-   Reporting
    
-   Audit
    
-   WebSocket Live Updates
    
-   zukünftige Erweiterungen
    

----------

# 15. Execution Runtime

Die Runtime ist für die tatsächliche Ausführung zuständig.

Sie übernimmt:

-   Graph Loading
    
-   Validation
    
-   Scheduling
    
-   Dependency Resolution
    
-   Node Execution
    
-   Output Propagation
    
-   Parallel Execution
    
-   Retry
    
-   Timeout
    
-   Cancellation
    
-   Error Handling
    
-   State Management
    
-   Event Emission
    
-   Observability
    

Die Runtime muss deterministische Zustandsübergänge besitzen.

----------

# 16. Execution States

Execution States:

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

Node Execution States:

```text
PENDING
READY
RUNNING
COMPLETED
FAILED
SKIPPED
CANCELLED
```

----------

# 17. Retry and Error Handling

Fehlerbehandlung ist Bestandteil der Runtime.

Die Engine soll unterscheiden können zwischen:

-   transienten Fehlern
    
-   permanenten Fehlern
    
-   Konfigurationsfehlern
    
-   Validierungsfehlern
    
-   Providerfehlern
    
-   Timeouts
    
-   Cancellation
    

Retry soll konfigurierbar sein:

-   maximale Versuche
    
-   Backoff
    
-   Backoff-Strategie
    
-   retrybare Fehler
    

Nodes sollen nicht jeweils eigene inkompatible Retry-Systeme implementieren müssen.

----------

# 18. Cancellation

Executions müssen vom Benutzer beendet werden können.

Cancellation muss von der Runtime an laufende Nodes weitergegeben werden.

Nodes sollen kooperativ auf Cancellation reagieren können.

----------

# 19. Pause and Resume

Die Runtime soll grundsätzlich pausierbare Executions ermöglichen.

Das ist insbesondere für zukünftige Human-in-the-loop-Funktionen erforderlich.

Ein Execution-Zustand kann daher beispielsweise sein:

```text
RUNNING
  ↓
WAITING
  ↓
RESUMED
```

----------

# 20. LLM Architecture

LLM Nodes dürfen nicht direkt an einzelne Provider gekoppelt sein.

Architektur:

```text
LLM Node
  ↓
LLM Service
  ↓
Provider Abstraction
  ↓
Provider Adapter / LiteLLM
  ↓
LLM Provider
```

Der Node soll unter anderem unterstützen:

-   Model
    
-   System Prompt
    
-   Messages
    
-   Temperature
    
-   Max Tokens
    
-   Structured Output
    
-   JSON Output
    
-   Streaming
    
-   Timeout
    
-   Retry
    
-   Credentials
    

----------

# 21. Prompt System

Prompts sollen Templates unterstützen.

Beispiel:

```text
Analyze the following lead:

{{ lead }}
```

Expressions und Variablen müssen sicher und deterministisch aufgelöst werden.

Arbitrary Python execution über Expressions ist nicht erlaubt.

Es darf kein unsicheres `eval()` für Benutzerinput verwendet werden.

----------

# 22. Expression System

Nodes müssen auf Outputs anderer Nodes zugreifen können.

Beispiel:

```text
{{ nodes.search.output.results }}
```

Das Expression-System muss:

-   typisiert
    
-   validierbar
    
-   sicher
    
-   deterministisch
    
-   nachvollziehbar
    

sein.

----------

# 23. Node Ports and Types

Node Connections basieren auf Ports.

Ein Port besitzt unter anderem:

-   Name
    
-   Richtung
    
-   Datentyp
    
-   Required
    
-   Nullable
    
-   Multiple
    
-   Schema
    

Mögliche Basistypen:

-   String
    
-   Number
    
-   Boolean
    
-   JSON
    
-   Object
    
-   List
    
-   Binary
    
-   File
    
-   Artifact
    
-   SecretReference
    

Verbindungen müssen validiert werden.

----------

# 24. Credentials and Secrets

Secrets sind strikt von Project-Konfigurationen getrennt.

Projects dürfen keine Klartext-Secrets enthalten.

Nodes referenzieren Credentials beispielsweise über:

```text
credential_ref
```

Credentials werden durch einen zentralen Credential Store verwaltet.

Secrets müssen verschlüsselt gespeichert werden.

Secrets dürfen niemals ungefiltert in:

-   Logs
    
-   Reports
    
-   API Responses
    
-   Execution History
    
-   Project Exports
    

erscheinen.

----------

# 25. Python Node System

Entwickler sollen eigene Python Nodes erstellen können.

Die Engine stellt dafür ein SDK bereit.

Das SDK definiert:

-   Node Base Class
    
-   Node Metadata
    
-   Input Definition
    
-   Output Definition
    
-   Configuration Schema
    
-   Execution Context
    
-   Logging
    
-   Metrics
    
-   Credentials
    
-   Events
    
-   Error Handling
    

Eigene Nodes müssen dem SDK Contract entsprechen.

----------

# 26. Trusted Code Model

Python Nodes werden in v1 grundsätzlich als vertrauenswürdiger Code behandelt.

Die Engine darf nicht suggerieren, dass beliebiger Python-Code innerhalb des normalen Runtime-Prozesses sicher sandboxed ist.

Das Plugin-System muss jedoch so abstrahiert sein, dass zukünftige isolierte Execution-Modelle möglich sind.

Mögliche spätere Isolation:

-   separater Prozess
    
-   Container
    
-   Worker
    
-   Sandbox
    

Diese Isolation ist nicht Bestandteil der v1-Funktionalität, die Architektur darf sie aber nicht verhindern.

----------

# 27. Plugin System

Nodes können über Plugins erweitert werden.

Plugins enthalten:

-   Metadata
    
-   Version
    
-   Dependencies
    
-   Node Implementations
    
-   Compatibility Information
    
-   Documentation
    

Plugin Lifecycle:

```text
Discover
  ↓
Validate
  ↓
Install
  ↓
Register
  ↓
Load
  ↓
Use
  ↓
Disable
  ↓
Remove
```

Plugins müssen Engine- und SDK-Kompatibilität deklarieren.

----------

# 28. Project Dependencies

Projects deklarieren benötigte Nodes und Plugins.

Beim Import oder Aktivieren eines Projects prüft die Engine:

-   Node vorhanden?
    
-   Plugin vorhanden?
    
-   Version kompatibel?
    
-   Configuration gültig?
    
-   Ports kompatibel?
    
-   Dependencies erfüllt?
    

Projects dürfen keine impliziten Abhängigkeiten besitzen.

----------

# 29. Project Import and Export

Projects sollen als portable Packages exportiert werden können.

Ein Project Package enthält beispielsweise:

-   Manifest
    
-   Project Metadata
    
-   Graph
    
-   Node Configuration
    
-   Prompts
    
-   Templates
    
-   Dependencies
    
-   Project Resources
    
-   Documentation
    

Ein Export enthält niemals ungeschützte Credentials oder Secrets.

Beim Import erfolgt eine vollständige Validierung.

----------

# 30. Project Versioning

Projects besitzen Versionen.

Eine Execution referenziert immer:

```text
project_id
project_version
```

Die Engine muss alte Project-Versionen nachvollziehbar halten, soweit sie noch für Execution History oder Reproduzierbarkeit benötigt werden.

----------

# 31. Observability

Observability ist ein Kernbestandteil.

Die Engine muss mindestens unterstützen:

## Logs

Strukturierte Logs für:

-   Engine
    
-   Project
    
-   Execution
    
-   Node
    
-   Trigger
    

## Metrics

Beispielsweise:

-   Execution Count
    
-   Success Rate
    
-   Failure Rate
    
-   Duration
    
-   Node Duration
    
-   Token Usage
    
-   Estimated Cost
    
-   Active Executions
    

## Traces

Der Weg einer Execution durch den Graph soll nachvollziehbar sein.

----------

# 32. Execution Inspector

Benutzer sollen eine Execution vollständig untersuchen können.

Eine Execution zeigt:

```text
Execution
├── Status
├── Trigger
├── Project Version
├── Start
├── End
├── Duration
├── Cost
├── Tokens
├── Nodes
│   ├── Input
│   ├── Output
│   ├── Logs
│   ├── Error
│   ├── Duration
│   └── Metrics
└── Errors
```

Große Payloads können als separate Artifacts gespeichert werden.

----------

# 33. Cost Tracking

AI Executions sollen Tokenverbrauch und Kosten nachvollziehbar machen.

Mindestens:

-   Provider
    
-   Model
    
-   Input Tokens
    
-   Output Tokens
    
-   Total Tokens
    
-   Estimated Cost
    
-   Currency
    

Wenn Kosten nur geschätzt werden können, muss dies entsprechend gekennzeichnet werden.

----------

# 34. Reporting

Die Plattform soll technische und fachliche Reports ermöglichen.

Technische Reports:

-   Execution Report
    
-   Error Report
    
-   Performance Report
    
-   Cost Report
    

Project-/Business-Reports können beispielsweise enthalten:

-   Anzahl verarbeiteter Datensätze
    
-   Anzahl Leads
    
-   Anzahl erfolgreicher Aktionen
    
-   Anzahl Fehler
    
-   Laufzeit
    
-   Kosten
    

Reports sollen auf Metrics und Execution Data aufbauen.

----------

# 35. Monitoring

Das Frontend stellt ein Monitoring Dashboard bereit.

Das Dashboard zeigt unter anderem:

-   aktive Executions
    
-   vergangene Executions
    
-   Erfolgsquote
    
-   Fehler
    
-   Laufzeiten
    
-   Tokenverbrauch
    
-   Kosten
    
-   aktuelle Fehler
    
-   Execution-Aktivität
    

Monitoring muss sowohl auf Project- als auch auf Engine-Ebene möglich sein.

----------

# 36. Frontend Vision

Das Frontend ist eine visuelle Entwicklungs- und Betriebsumgebung für die Engine.

Es soll mindestens folgende Bereiche besitzen:

```text
Projects
Editor
Node Library
Inspector
Executions
Monitoring
Reports
Credentials
Plugins
Settings
```

----------

# 37. Visual Graph Editor

Der Graph Editor ist das zentrale UI.

Benutzer können:

-   Nodes hinzufügen
    
-   Nodes verschieben
    
-   Nodes löschen
    
-   Nodes duplizieren
    
-   Nodes verbinden
    
-   Connections entfernen
    
-   Nodes konfigurieren
    
-   Graph validieren
    
-   Graph speichern
    
-   Graph veröffentlichen
    
-   Graph ausführen
    

Der Editor unterstützt:

-   Zoom
    
-   Pan
    
-   Minimap
    
-   Selection
    
-   Context Menu
    
-   Keyboard Shortcuts
    
-   Undo
    
-   Redo
    
-   Copy
    
-   Paste
    
-   Autosave
    

----------

# 38. Node Library

Die Node Library wird aus der Node Registry gespeist.

Nodes werden kategorisiert.

Benutzer können Nodes suchen und filtern.

Neue Plugins sollen automatisch neue Nodes in der Library bereitstellen.

----------

# 39. Node Inspector

Der Inspector zeigt die Konfiguration des ausgewählten Nodes.

Der Inspector basiert möglichst auf dem Configuration Schema des Nodes.

Dadurch können neue Nodes integriert werden, ohne für jeden Node eine vollständig individuelle Frontend-Implementierung zu benötigen.

----------

# 40. Live Execution

Während einer Execution soll der Benutzer den Graph-Lauf live beobachten können.

Beispiel:

```text
Trigger        ✓
Search         ✓
LLM            ● running
Qualification  ○
Email          ○
```

Die UI erhält Live-Events über WebSocket oder eine vergleichbare Echtzeitverbindung.

----------

# 41. Developer Experience

Die Plattform soll eine klare Developer Experience besitzen.

Ein Entwickler soll:

1.  SDK installieren
    
2.  Node implementieren
    
3.  Node testen
    
4.  Node validieren
    
5.  Plugin paketieren
    
6.  Plugin installieren
    
7.  Node im Editor verwenden
    

können.

Die Node-Entwicklung soll vollständig dokumentiert und testbar sein.

----------

# 42. CLI

Neben der Web UI soll langfristig eine CLI verfügbar sein.

Beispielsweise:

```text
agent-engine project validate
agent-engine project import
agent-engine project export
agent-engine project run

agent-engine node create
agent-engine node validate
agent-engine node test
agent-engine node package

agent-engine backup
agent-engine restore
```

CLI, API und GUI sollen dieselben Application Services verwenden.

----------

# 43. API

Das Backend stellt eine versionierte API bereit.

Beispielsweise:

```text
/api/v1/projects
/api/v1/nodes
/api/v1/executions
/api/v1/triggers
/api/v1/credentials
/api/v1/plugins
/api/v1/reports
```

Live Execution Events werden über WebSocket bereitgestellt.

Das API-Schema soll maschinenlesbar sein und als Grundlage für einen typed Frontend Client dienen.

----------

# 44. Persistence

Die Standard-Persistenz besteht konzeptionell aus:

```text
PostgreSQL
    ↓
Projects
Executions
Events
Metrics
Credentials Metadata
Reports
Plugins

Redis
    ↓
Queues
Ephemeral State
Pub/Sub

File/Object Storage
    ↓
Artifacts
Files
Large Payloads
```

Für lokale Entwicklung und einfache Installationen kann eine vereinfachte Infrastruktur verwendet werden.

----------

# 45. Self-hosted First

Die Plattform ist primär self-hosted.

Das bedeutet:

-   keine zwingende Cloud-Abhängigkeit
    
-   lokale Installation
    
-   Docker-Unterstützung
    
-   persistente Daten
    
-   eigene API Keys
    
-   eigene LLM Provider
    
-   eigene Infrastruktur
    

Ein Unternehmen soll die Engine innerhalb seiner eigenen Infrastruktur betreiben können.

----------

# 46. Deployment

Die v1 soll mit einer klar definierten Self-hosted Deployment-Struktur ausgeliefert werden.

Mindestens:

```text
Frontend
Backend
PostgreSQL
Redis
Persistent Storage
```

Ein Reverse Proxy kann vorgeschaltet werden.

Health- und Readiness-Endpoints sind erforderlich.

----------

# 47. Backup and Restore

Die Anwendung muss Möglichkeiten für Backup und Restore bereitstellen.

Zu berücksichtigen sind:

-   Datenbank
    
-   Projects
    
-   Project Versions
    
-   Credentials
    
-   Artifacts
    
-   Konfiguration
    

Secrets müssen über einen kontrollierten Schlüsselmechanismus gesichert werden.

----------

# 48. Security

Security ist Bestandteil der Architektur.

Mindestens:

-   Authentifizierung
    
-   verschlüsselte Credentials
    
-   Secret Redaction
    
-   sichere Expression Engine
    
-   Input Validation
    
-   Output Validation
    
-   API Validation
    
-   sichere Dateioperationen
    
-   kontrollierte Plugin-Ausführung
    
-   Auditierbarkeit sicherheitsrelevanter Aktionen
    

----------

# 49. Authentication

v1 benötigt eine grundlegende lokale Authentifizierung.

Die Architektur soll spätere Authentifizierungsmechanismen nicht verhindern.

Enterprise SSO ist ausdrücklich nicht Bestandteil von v1.

----------

# 50. Authorization

v1 benötigt keine vollständige Enterprise-RBAC-Infrastruktur.

Ressourcen müssen jedoch bereits so modelliert werden, dass spätere Benutzer-, Rollen- und Berechtigungsmodelle ergänzt werden können.

----------

# 51. Audit

Wichtige Systemaktionen sollen nachvollziehbar sein.

Beispielsweise:

-   Project erstellt
    
-   Project geändert
    
-   Project veröffentlicht
    
-   Project gelöscht
    
-   Credential erstellt
    
-   Credential geändert
    
-   Plugin installiert
    
-   Execution gestartet
    
-   Execution beendet
    
-   Execution abgebrochen
    

----------

# 52. Performance

Die Engine soll von Anfang an asynchron und nicht-blockierend für I/O-intensive Workloads konzipiert werden.

Besonders relevant:

-   LLM Requests
    
-   HTTP Requests
    
-   Web Search
    
-   SMTP
    
-   Database
    
-   File Operations
    

Die Architektur soll parallele Node-Ausführung ermöglichen.

----------

# 53. Scalability Boundary

v1 ist als Single-Installation ausgelegt.

Die Engine soll jedoch intern so strukturiert sein, dass später Execution Worker eingeführt werden können.

Zukünftige Architektur:

```text
API
 ↓
Scheduler
 ↓
Queue
 ↓
Workers
```

Ein verteiltes Cluster ist ausdrücklich nicht Bestandteil von v1.

----------

# 54. Reliability

Die Engine soll fehlertolerant arbeiten.

Dazu gehören:

-   Retry
    
-   Timeout
    
-   Cancellation
    
-   persistente Execution States
    
-   strukturierte Fehler
    
-   Recovery-Mechanismen
    
-   Health Checks
    
-   Transaktionsgrenzen
    
-   nachvollziehbare Execution History
    

----------

# 55. Determinism and Reproducibility

Die Engine soll soweit technisch möglich reproduzierbare Executions ermöglichen.

Dazu gehören:

-   Project Version
    
-   Node Version
    
-   Plugin Version
    
-   Configuration Snapshot
    
-   Trigger Information
    
-   Runtime Metadata
    

AI Provider können naturgemäß nicht vollständig deterministisch sein.

Die Engine muss daher zwischen:

-   reproduzierbarer Konfiguration
    
-   tatsächlichem AI Output
    

unterscheiden.

----------

# 56. Compatibility

Folgende Versionen müssen explizit berücksichtigt werden:

```text
Engine Version
Project Schema Version
Project Version
Node Version
SDK Version
Plugin Version
API Version
```

Kompatibilitätsprüfungen sollen automatisiert erfolgen.

----------

# 57. Testing Philosophy

Jede zentrale Engine-Funktion muss automatisiert getestet werden.

Erforderlich sind:

-   Unit Tests
    
-   Integration Tests
    
-   API Tests
    
-   Contract Tests
    
-   Node Tests
    
-   Frontend Tests
    
-   End-to-End Tests
    

Beispielprojects dienen zusätzlich als Integrationstest-Suite.

----------

# 58. Example Projects

Die Repository-Struktur soll Beispielprojects enthalten.

Mindestens:

## Hello World

```text
Manual Trigger
  ↓
Transform
  ↓
Output
```

## LLM

```text
Input
  ↓
LLM
  ↓
Output
```

## Web Research

```text
Trigger
  ↓
Web Search
  ↓
LLM
  ↓
Report
```

## Lead Generation

```text
Cron
  ↓
Search
  ↓
Qualification
  ↓
Scoring
  ↓
Email
```

Diese Projects dienen gleichzeitig als Dokumentation und Regressionstests.

----------

# 59. Documentation Philosophy

Die Plattform muss vollständig dokumentiert werden.

Dokumentation soll mindestens umfassen:

-   Vision
    
-   Architektur
    
-   Domain Model
    
-   Node System
    
-   Execution Engine
    
-   Project Format
    
-   Plugin System
    
-   SDK
    
-   API
    
-   Deployment
    
-   Security
    
-   Testing
    
-   Operations
    
-   Roadmap
    
-   Architecture Decision Records
    

Architekturentscheidungen werden in ADRs dokumentiert.

----------

# 60. Version 1 Scope

Version 1 umfasst:

### Engine

-   Core Domain
    
-   Project System
    
-   Graph System
    
-   Node System
    
-   Execution Runtime
    
-   Scheduler
    
-   Trigger
    
-   Event System
    
-   Persistence
    
-   API
    
-   WebSockets
    
-   Credentials
    
-   Secrets
    
-   Observability
    
-   Reporting
    
-   Authentication
    
-   Audit
    
-   Backup/Restore
    
-   CLI
    

### AI

-   LLM Abstraction
    
-   LiteLLM Integration
    
-   OpenAI
    
-   Anthropic
    
-   Ollama
    
-   vLLM
    
-   Structured Outputs
    
-   Prompt Templates
    
-   Token Tracking
    
-   Cost Tracking
    
-   Agent Node
    

### Nodes

-   Core Nodes
    
-   Control Flow
    
-   LLM
    
-   Agent
    
-   HTTP
    
-   Web Search
    
-   SMTP / Email
    
-   Files
    
-   JSON
    
-   CSV
    
-   Database
    
-   Webhook
    
-   Logging
    

### Extensibility

-   Node SDK
    
-   Python Nodes
    
-   Plugin System
    
-   Project Dependencies
    
-   Project Import / Export
    

### Frontend

-   Project Management
    
-   Graph Editor
    
-   Node Library
    
-   Node Inspector
    
-   Validation
    
-   Drafts
    
-   Publishing
    
-   Execution UI
    
-   Live Execution
    
-   Logs
    
-   Monitoring
    
-   Reports
    
-   Credentials
    
-   Plugin Management
    
-   Settings
    

----------

# 61. Explicitly Out of Scope for Version 1

Folgende Funktionen werden nicht Bestandteil von v1:

-   Multi-Tenant SaaS
    
-   Billing
    
-   Marketplace
    
-   Mobile App
    
-   Kubernetes Operator
    
-   verteiltes Cluster
    
-   Enterprise SSO
    
-   komplexes Enterprise-RBAC
    
-   komplexes Agent Memory
    
-   eigener Vector Database Layer
    
-   Browser Automation
    
-   Multi-Agent Collaboration
    

Diese Funktionen dürfen durch die Architektur später unterstützt werden, werden aber nicht als v1-Funktionalität implementiert.

----------

# 62. Future Extension Areas

Die Architektur soll spätere Erweiterungen ermöglichen:

-   Multi-Tenancy
    
-   Advanced RBAC
    
-   Enterprise SSO
    
-   Distributed Workers
    
-   Kubernetes
    
-   Marketplace
    
-   Agent Memory
    
-   Vector Stores
    
-   Browser Automation
    
-   Multi-Agent Communication
    
-   zusätzliche Trigger
    
-   zusätzliche Provider
    
-   zusätzliche Node Types
    
-   zusätzliche Storage Backends
    
-   zusätzliche Execution Backends
    

Diese Erweiterungen dürfen die v1-Architektur nicht unnötig verkomplizieren.

----------

# 63. Non-Goals

Die Engine ist nicht:

-   nur ein Chatbot
    
-   nur ein Prompt Builder
    
-   nur ein Workflow Editor
    
-   nur eine LangChain-Oberfläche
    
-   nur ein LLM Gateway
    
-   nur ein Monitoring Dashboard
    
-   nur ein Python Plugin Loader
    

Die Engine verbindet diese Fähigkeiten zu einer eigenständigen Runtime-Plattform.

----------

# 64. Architectural North Star

Alle zukünftigen Architekturentscheidungen sollen auf folgendes Modell zurückgeführt werden:

```text
                    ENGINE
                       │
        ┌──────────────┼──────────────┐
        │              │              │
     PROJECT        NODE SYSTEM    RUNTIME
        │              │              │
      GRAPH        REGISTRY        EXECUTION
        │              │              │
      NODES         PLUGINS         EVENTS
        │              │              │
        └──────────────┼──────────────┘
                       │
                 OBSERVABILITY
                       │
              ┌────────┼────────┐
              │        │        │
             LOGS    METRICS   REPORTS
```

Die zentrale Kette lautet:

```text
Project
  ↓
Project Version
  ↓
Graph
  ↓
Nodes
  ↓
Execution
  ↓
Events
  ↓
Observability
```

----------

# 65. Core Principles

Die folgenden Prinzipien sind für die gesamte Entwicklung verbindlich:

1.  **Engine first.**
    
2.  **Projects sind deklarative Agent-Anwendungen.**
    
3.  **Nodes sind standardisierte Erweiterungseinheiten.**
    
4.  **Graphs sind die zentrale Orchestrierungsabstraktion.**
    
5.  **Executions arbeiten gegen immutable Project-Version-Snapshots.**
    
6.  **Runtime und Project bleiben getrennt.**
    
7.  **LLM Provider werden abstrahiert.**
    
8.  **Credentials und Secrets bleiben von Projects getrennt.**
    
9.  **Frontend und Backend teilen einen definierten Contract.**
    
10.  **Node-Konfigurationen sind schema-driven.**
    
11.  **Observability wird von Anfang an integriert.**
    
12.  **Fehlerbehandlung gehört in die Runtime.**
    
13.  **Python Nodes verwenden ein definiertes SDK.**
    
14.  **Plugins sind versioniert und kompatibelkeitsgeprüft.**
    
15.  **API, CLI und GUI verwenden dieselbe Application Logic.**
    
16.  **Security darf nicht nachträglich ergänzt werden.**
    
17.  **Versionierung und Migrationen werden von Anfang an berücksichtigt.**
    
18.  **v1 bleibt bewusst Single-Installation statt unnötig früh verteilt zu werden.**
    
19.  **Komplexität wird nur eingeführt, wenn sie einen konkreten Nutzen besitzt.**
    
20.  **Neue Architekturentscheidungen dürfen die Kernabstraktionen Node, Graph, Project und Execution nicht ohne dokumentierte Begründung brechen.**
    

----------

# 66. Definition of Success

Die Vision ist erreicht, wenn ein Unternehmen die Engine self-hosted betreiben kann und ein Benutzer ohne eigene Orchestrierungsinfrastruktur ein vollständiges Agentensystem erstellen kann:

```text
Engine installieren
        ↓
Project erstellen
        ↓
Nodes auswählen
        ↓
Graph erstellen
        ↓
Konfiguration setzen
        ↓
Credentials hinterlegen
        ↓
Project validieren
        ↓
Project veröffentlichen
        ↓
Trigger konfigurieren
        ↓
Execution starten
        ↓
Live überwachen
        ↓
Logs untersuchen
        ↓
Kosten analysieren
        ↓
Report auswerten
        ↓
Project exportieren
```

Ein Entwickler muss gleichzeitig in der Lage sein:

```text
Python SDK
    ↓
Custom Node
    ↓
Plugin
    ↓
Node Registry
    ↓
Visual Editor
    ↓
Project
    ↓
Execution Runtime
```

zu durchlaufen, ohne die Engine selbst verändern zu müssen.

Das langfristige Ziel ist eine stabile, erweiterbare und provider-agnostische Runtime, auf der Unternehmen ihre eigenen AI-Agent-Systeme entwickeln und betreiben können.