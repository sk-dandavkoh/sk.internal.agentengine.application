# Backend Engineering Guidelines

## 1. Geltungsbereich

Diese Datei ergänzt die globalen Regeln aus `/AGENTS.md` um verbindliche Regeln für das Backend.

Vor jeder Backend-Arbeit sind mindestens diese Dokumente zu lesen:

1. `/AGENTS.md`
2. `/backend/AGENTS.md`
3. `/docs/vision.md`
4. `/docs/architecture.md`
5. `/docs/roadmap-backend.md`

Bei Widersprüchen gilt:

1. explizite Projektanforderung
2. `/AGENTS.md`
3. `/backend/AGENTS.md`
4. `/docs/architecture.md`
5. `/docs/vision.md`
6. `/docs/roadmap-backend.md`

Diese Datei wiederholt bewusst keine allgemeinen Regeln aus `/AGENTS.md`.

---

# 2. Backend-Ziel

Das Backend ist die autoritative technische Instanz der Agent Engine.

Es ist verantwortlich für:

- Domainmodell
- Workflow- und Versionsverwaltung
- Graphvalidierung
- Node Contracts
- Node Registry
- Execution Runtime
- Scheduling
- Trigger
- Events
- Persistenz
- Credentials und Secrets
- AI-/LLM-Abstraktion
- Plugins
- Reports
- Authentifizierung und Autorisierung
- CLI/Application Services
- Worker-/Queue-Architektur
- Observability
- Recovery
- Security
- API-Verträge

Das Frontend darf keine fachliche Wahrheit duplizieren.

Insbesondere dürfen folgende Regeln nicht ausschließlich im Frontend implementiert werden:

- Graphvalidierung
- Port-Kompatibilität
- Required-Input-Prüfung
- Config-Validierung
- Workflowstatus
- Publish-Regeln
- Versionierung
- Execution-State-Transitions
- Berechtigungsprüfung
- Credential-Auflösung
- Resource Limits
- Sicherheitsregeln

Das Frontend kann dieselben Regeln für UX-Zwecke spiegeln, aber das Backend bleibt autoritativ.

---

# 3. Technologieprinzipien

Das Backend verwendet grundsätzlich:

- Python
- `uv` für Dependency- und Environment-Management
- FastAPI für HTTP/API
- Pydantic für API- und Konfigurationsschemas
- SQLAlchemy für relationale Persistenz
- Alembic für Datenbankmigrationen
- PostgreSQL als primäre relationale Datenbank
- Redis, sofern für Queue-/Worker-/Runtime-Anforderungen erforderlich
- pytest für Tests
- Ruff für Linting und Formatting
- mypy oder den im Projekt festgelegten Type Checker
- Testcontainers für realistische Infrastrukturtests

Konkrete Versionen werden nicht geraten oder ungeprüft eingeführt.

Neue Dependencies müssen einen nachvollziehbaren architektonischen Nutzen haben.

Keine Dependency hinzufügen, nur weil sie eine kleine Implementierung bequemer macht.

---

# 4. Python-Standards

## 4.1 Typisierung

Python-Code ist grundsätzlich vollständig typisiert.

Neue öffentliche APIs, Klassen, Funktionen und Methoden benötigen Type Hints.

Bevorzugt werden:

- moderne Python-Type-Syntax
- `dataclass` für geeignete interne Datenstrukturen
- Pydantic für Boundary-/Schema-Modelle
- explizite Return Types
- `Enum` oder geeignete Value Objects für endliche Zustände

`Any` ist nur erlaubt, wenn es technisch begründet ist.

Unpräzise Typisierung darf nicht verwendet werden, um Type Errors zu unterdrücken.

Nicht verwenden:

```python
# type: ignore
```

ohne konkreten, dokumentierten Grund.

----------

## 4.2 Async

Async wird nur dort eingesetzt, wo es fachlich oder technisch sinnvoll ist.

Insbesondere:

-   FastAPI Request Handling
    
-   Datenbankzugriffe mit async Stack
    
-   externe I/O
    
-   HTTP
    
-   Redis
    
-   Worker-/Runtime-I/O
    

Keine künstliche Async-Schicht für rein synchrone CPU-Logik.

Keine blockierenden Operationen in Async-Code, sofern sie nicht bewusst ausgelagert werden.

CPU-intensive oder potentiell blockierende Operationen müssen hinsichtlich ihrer Auswirkungen auf den Event Loop bewertet werden.

----------

## 4.3 Exceptions

Exceptions sind Teil des technischen Vertrags.

Keine pauschalen:

```python
except Exception:
    ...
```

ohne anschließende sinnvolle Behandlung.

Fehler müssen:

-   semantisch klassifizierbar
    
-   beobachtbar
    
-   API-seitig sauber abbildbar
    
-   secret-safe
    
-   testbar
    

sein.

Business-/Domain-Fehler dürfen nicht ungefiltert als HTTP-500 erscheinen.

Interne technische Details dürfen nicht unkontrolliert an API-Clients gelangen.

----------

# 5. Backend-Architektur

Die Backend-Struktur folgt der in `/docs/architecture.md` definierten Architektur.

Grundsätzlich sind folgende Verantwortungsbereiche vorgesehen:

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

Die tatsächliche Projektstruktur darf davon abweichen, wenn die Architektur dadurch klarer wird. Verantwortlichkeiten müssen jedoch erhalten bleiben.

----------

# 6. Domain Layer

Der Domain Layer enthält fachliche Regeln und fachliche Modelle.

Er darf nicht von folgenden technischen Details abhängig sein:

-   FastAPI
    
-   HTTP
    
-   SQLAlchemy
    
-   Redis
    
-   konkreten Provider-SDKs
    
-   Frontend
    
-   Dateisystem
    
-   Docker
    
-   konkreten Infrastrukturimplementierungen
    

Domain-Code muss möglichst isoliert testbar sein.

## 6.1 Zentrale Domainobjekte

Unter anderem:

-   Workflow
    
-   WorkflowDraft
    
-   WorkflowVersion
    
-   Graph
    
-   NodeInstance
    
-   Edge
    
-   Trigger
    
-   Execution
    
-   NodeExecution
    
-   CredentialReference
    
-   PluginMetadata
    

Diese Modelle dürfen nicht einfach als Datenbanktabellen behandelt werden.

Ihre fachliche Semantik muss explizit bleiben.

----------

# 7. Draft vs. Published Version

Ein Workflow Draft ist veränderbar.

Eine veröffentlichte Workflow Version ist unveränderlich.

Grundregel:

```text
Draft
  ↓ validate
Published Version
  ↓ snapshot
Execution
```

Eine Execution darf niemals stillschweigend auf den aktuellsten Draft zugreifen.

Jede Execution muss eindeutig nachvollziehbar machen:

-   Workflow
    
-   Workflow Version
    
-   Node-Versionen
    
-   Plugin-Versionen
    
-   relevante Config
    
-   Trigger
    
-   Runtime-Metadaten
    

Änderungen am Draft dürfen laufende oder bereits abgeschlossene Executions nicht rückwirkend verändern.

----------

# 8. Application Layer

Application Services orchestrieren Use Cases.

Sie dürfen:

-   Domainobjekte laden
    
-   Domainlogik aufrufen
    
-   Transaktionen koordinieren
    
-   Repositories verwenden
    
-   Events auslösen
    
-   Infrastruktur abstrahiert verwenden
    

Sie sollen nicht:

-   HTTP-spezifische Details enthalten
    
-   SQL direkt ausführen
    
-   Frontend-Annahmen enthalten
    
-   fachliche Regeln nur als Controller-Logik implementieren
    

Ein Use Case sollte als klar benennbare Operation erkennbar sein.

Beispiele:

```text
CreateWorkflow
UpdateWorkflowDraft
ValidateWorkflow
PublishWorkflow
StartExecution
CancelExecution
RetryExecution
CreateTrigger
InstallPlugin
ExportWorkflow
ImportWorkflow
GenerateExecutionReport
```

----------

# 9. API Layer

FastAPI dient als Transport-/API-Schicht.

Controller/Routers müssen dünn bleiben.

Typischer Ablauf:

```text
HTTP Request
    ↓
Request Schema
    ↓
Application Service
    ↓
Domain
    ↓
Infrastructure
    ↓
Response DTO
    ↓
HTTP Response
```

Kein umfangreicher Business-Code in Routern.

Keine direkten SQLAlchemy-Abfragen aus API-Routern.

Keine direkte Node-Ausführung aus HTTP-Handlern.

----------

# 10. API-Versionierung

Die öffentliche API beginnt unter:

```text
/api/v1
```

Breaking Changes dürfen nicht unkontrolliert in bestehende Endpunkte eingeführt werden.

API-Schemas müssen explizit definiert werden.

Interne Domainmodelle werden nicht automatisch als API-Schemas exponiert.

----------

# 11. API-Fehler

Fehlerantworten müssen strukturiert sein.

Mindestens müssen Fehler unterscheiden können zwischen:

-   Validation Error
    
-   Authentication Error
    
-   Authorization Error
    
-   Not Found
    
-   Conflict
    
-   Domain Error
    
-   Configuration Error
    
-   Execution Error
    
-   Provider Error
    
-   Internal Error
    

Interne Exception-Stacks gehören nicht in normale Production-Responses.

Correlation IDs müssen bei relevanten Fehlern nachvollziehbar bleiben.

----------

# 12. Pydantic

Pydantic wird primär an Systemgrenzen verwendet:

-   HTTP Requests
    
-   HTTP Responses
    
-   Konfiguration
    
-   externe Provider-Schemas
    
-   Plugin-Manifeste
    
-   Import-/Export-Schemas
    

Pydantic-Modelle sind nicht automatisch Domainmodelle.

Nicht jede interne Domainstruktur muss durch Pydantic modelliert werden.

----------

# 13. Datenbank

PostgreSQL ist die primäre relationale Persistenz.

SQLAlchemy wird für Datenbankzugriffe verwendet.

Alembic ist die Quelle für Schema-Migrationen.

## 13.1 Regeln

Jede Schemaänderung benötigt:

-   Migration
    
-   passende Tests
    
-   Berücksichtigung bestehender Daten
    
-   nachvollziehbaren Upgrade-Pfad
    

Keine manuellen Schemaänderungen außerhalb des Migrationssystems.

Keine automatische Produktionsmigration durch zufällige ORM-Schemaerzeugung.

Nicht verwenden:

```python
Base.metadata.create_all(...)
```

als Produktions-Migrationsstrategie.

----------

# 14. Migrationen

Migrationen müssen:

-   deterministisch
    
-   reproduzierbar
    
-   auf einer leeren Datenbank ausführbar
    
-   auf einer realistischen bestehenden Datenbank ausführbar
    
-   reversibel sein, sofern technisch sinnvoll
    
-   getestet werden
    

Migrationen dürfen keine impliziten Produktionsannahmen enthalten.

Bei potentiell großen Tabellen müssen Locking-, Laufzeit- und Downtime-Auswirkungen berücksichtigt werden.

----------

# 15. Repository Pattern

Repositories kapseln Persistenzzugriffe dort, wo dies architektonisch sinnvoll ist.

Application- und Domain-Code soll nicht von konkreten SQLAlchemy-Details abhängen.

Repository-Interfaces gehören bei Bedarf in eine geeignete abstrakte Schicht.

Repository-Implementierungen gehören in die Infrastructure-Schicht.

Keine Repository-Abstraktion nur aus Dogmatismus.

Sie muss eine klare Verantwortungsgrenze schaffen.

----------

# 16. Transaktionen

Transaktionsgrenzen müssen bewusst definiert werden.

Besonders wichtig:

-   Workflow Draft Änderungen
    
-   Publish
    
-   Execution-Erstellung
    
-   NodeExecution-State-Änderungen
    
-   Credential Änderungen
    
-   Plugin Installation
    
-   Import
    
-   Recovery
    

Keine lang laufenden AI-/HTTP-/Node-Ausführungen innerhalb einer offenen Datenbanktransaktion.

Externe Seiteneffekte und Datenbanktransaktionen müssen sauber voneinander getrennt werden.

----------

# 17. Execution Runtime

Die Execution Runtime ist eine zentrale Backend-Komponente.

Sie ist verantwortlich für:

-   Laden eines Workflow-Version-Snapshots
    
-   Validierung
    
-   Scheduling
    
-   Dependency Resolution
    
-   Node Execution
    
-   Output Propagation
    
-   Parallelisierung
    
-   Retry
    
-   Timeout
    
-   Cancellation
    
-   State Management
    
-   Events
    
-   Observability
    
-   Fehlerbehandlung
    

Die Runtime darf nicht von FastAPI abhängen.

Sie muss auch durch:

-   CLI
    
-   Worker
    
-   Tests
    
-   interne Application Services
    

verwendbar sein.

----------

# 18. Execution State Machine

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

Node States:

```text
PENDING
READY
RUNNING
WAITING
COMPLETED
FAILED
SKIPPED
CANCELLED
```

Transitions müssen explizit definiert sein.

Ungültige State Transitions müssen verhindert werden.

Keine beliebigen String-Zuweisungen:

```python
execution.status = "completed"
```

wenn dadurch die fachliche State Machine umgangen wird.

Bevorzugt:

```python
execution.complete(...)
```

oder eine äquivalente explizite Transition.

----------

# 19. Node Contract

Jeder Node muss einen klaren Contract besitzen.

Ein Node Contract beschreibt mindestens:

-   Node ID
    
-   Name
    
-   Beschreibung
    
-   Version
    
-   Inputs
    
-   Outputs
    
-   Config Schema
    
-   Capabilities
    
-   Validierung
    
-   Runtime-Verhalten
    
-   Dokumentation
    

Ports müssen explizit beschrieben werden.

Beispielhafte Port-Typen:

```text
String
Number
Boolean
JSON
Object
List
Binary
File
Artifact
SecretReference
```

Node-Ausführung darf nicht von impliziten Dict-Konventionen abhängen.

----------

# 20. Node Inputs und Outputs

Inputs und Outputs müssen semantisch typisiert sein.

Ein Port muss mindestens unterscheiden können:

-   Name
    
-   Richtung
    
-   Typ
    
-   Required
    
-   Nullable
    
-   Multiple
    
-   Schema
    

Type Compatibility wird zentral validiert.

Das Frontend darf keine eigene inkompatible Definition der Port-Kompatibilität entwickeln.

----------

# 21. Node Execution Context

Nodes erhalten einen kontrollierten Execution Context.

Dieser kann abhängig von der Phase enthalten:

-   Execution ID
    
-   Node Execution ID
    
-   Workflow ID
    
-   Workflow Version
    
-   Logger
    
-   Metrics
    
-   Credential Resolver
    
-   Event Publisher
    
-   Resource Limits
    
-   Cancellation State
    
-   Runtime Metadata
    

Nodes dürfen nicht direkt auf globale Application State-Objekte zugreifen.

Keine globalen Singleton-Zustände für Execution-spezifische Daten.

----------

# 22. Built-in Nodes

Built-in Nodes werden über die Node Registry aufgelöst.

Beispielhafte Nodes:

```text
core.input
core.output
core.log
core.if
core.switch
core.delay
core.merge
core.http
core.web_search
core.smtp
```

Die endgültige Node-Liste ergibt sich aus Roadmap und Architektur.

Nodes müssen unabhängig von HTTP-Controllern ausführbar sein.

Jeder Built-in Node benötigt eigene Tests.

----------

# 23. Node Registry

Die Node Registry ist die zentrale Auflösungsinstanz.

Sie muss unter anderem ermöglichen:

```text
Node ID
    ↓
Node Definition
    ↓
Version
    ↓
Runtime Implementation
```

Keine verstreuten `if node_type == ...`-Ketten im Backend.

Neue Nodes müssen registrierbar sein, ohne zentrale Runtime-Logik unnötig zu verändern.

----------

# 24. Custom Python Nodes

Custom Python Nodes verwenden das definierte Python SDK.

Das SDK stellt geeignete Abstraktionen bereit für:

-   Node Metadata
    
-   Inputs
    
-   Outputs
    
-   Config
    
-   Execution Context
    
-   Logging
    
-   Metrics
    
-   Credentials
    
-   Events
    
-   Errors
    

V1:

```text
Custom Python Nodes = trusted code
```

Das darf nicht als Sandbox betrachtet werden.

Keine Behauptung, dass ein Python Node sicher isoliert sei, solange keine echte Prozess-/Container-Isolation existiert.

----------

# 25. Plugins

Plugins werden über definierte Manifeste und Lifecycle-Regeln verwaltet.

Mindestens:

```text
discover
validate
install
register
load
use
disable
remove
```

Plugins müssen hinsichtlich folgender Eigenschaften geprüft werden:

-   Engine-Kompatibilität
    
-   Plugin-Version
    
-   Dependencies
    
-   Node-Versionen
    
-   Manifest
    
-   Integrität
    
-   Konfiguration
    

V1 benötigt keinen Marketplace.

----------

# 26. AI-/LLM-Architektur

AI Provider müssen hinter einer Provider-Abstraktion liegen.

Zielstruktur:

```text
LLM Node
   ↓
AI / LLM Service
   ↓
Provider Abstraction
   ↓
Provider Adapter
   ↓
Provider
```

Die Node Runtime darf nicht direkt an einen einzelnen Anbieter gekoppelt werden.

LLM-Komponenten sollen mindestens folgende Konzepte sauber unterscheiden:

-   LLM Request
    
-   Message
    
-   Model
    
-   Provider
    
-   Tool Call
    
-   LLM Response
    
-   Token Usage
    
-   Provider Error
    

----------

# 27. Provider Tests

Normale CI-Tests dürfen nicht von externen kommerziellen AI APIs abhängig sein.

Stattdessen:

-   deterministische Fake Provider
    
-   Mock Provider
    
-   Contract Tests
    

Echte Provider-Integrationstests sind separat und explizit zu behandeln.

API Keys dürfen niemals in Repository, Tests, Logs oder Fixtures landen.

----------

# 28. Credentials und Secrets

Credentials werden zentral verwaltet.

Workflow Configs enthalten ausschließlich Referenzen.

Beispiel:

```text
credential_ref = "credential:openai-production"
```

Nicht:

```text
api_key = "sk-..."
```

Secrets dürfen niemals:

-   in Workflow JSON
    
-   in Workflow Exporten
    
-   in normalen Logs
    
-   in Reports
    
-   in Events
    
-   in Exceptions
    
-   in API Responses
    

erscheinen.

Secret-Auflösung erfolgt erst so spät wie sinnvoll.

----------

# 29. Secret Redaction

Redaction muss strukturell erfolgen.

Nicht nur bekannte Feldnamen filtern.

Potentiell sensible Werte müssen auch bei:

-   Exceptions
    
-   HTTP Debugging
    
-   Provider Responses
    
-   Node Outputs
    
-   Event Payloads
    
-   Logs
    
-   Reports
    

berücksichtigt werden.

Tests müssen explizit beweisen, dass Secrets nicht geleakt werden.

----------

# 30. Expression System

User-definierte Expressions müssen:

-   typisiert
    
-   sicher
    
-   deterministisch
    
-   begrenzt
    
-   testbar
    

sein.

Kein:

```python
eval(user_input)
```

Kein:

```python
exec(user_input)
```

Kein beliebiger Python-Code für Template-/Expression-Auswertung.

Expressions dürfen nicht unkontrolliert auf:

-   Dateisystem
    
-   Netzwerk
    
-   Environment
    
-   Python Runtime
    
-   Credentials
    

zugreifen.

----------

# 31. Prompt Templates

Prompt Templates können beispielsweise verwenden:

```text
{{ lead }}
{{ customer.name }}
{{ previous_result }}
```

Template Resolution muss:

-   deterministisch
    
-   typisiert
    
-   validierbar
    
-   fehlertolerant
    
-   secret-safe
    

sein.

Fehlende Variablen müssen klar behandelt werden.

Kein stilles Verschlucken von Konfigurationsfehlern.

----------

# 32. Trigger

Trigger führen nicht zu einer eigenen Execution-Logik.

Alle Trigger konvergieren auf denselben Execution Service.

Beispiel:

```text
Manual Trigger ─┐
API Trigger ────┤
Cron Trigger ───┤
Interval ───────┤
Webhook ────────┤
Event Trigger ──┘
        ↓
Execution Service
        ↓
Execution
```

Trigger dürfen keine Runtime-Logik duplizieren.

----------

# 33. Events

Events beschreiben Zustandsänderungen bzw. relevante Lifecycle-Ereignisse.

Beispiele:

```text
WorkflowCreated
WorkflowPublished
ExecutionCreated
ExecutionStarted
ExecutionCompleted
ExecutionFailed
NodeExecutionStarted
NodeExecutionCompleted
NodeExecutionFailed
```

Events sind nicht die alleinige kanonische State-Persistenz.

Der persistierte Domain-State bleibt autoritativ.

Event-Verarbeitung muss mit Duplicate Events und Wiederholungen umgehen können, sofern das System dies zulässt.

----------

# 34. Observability

Backend-Komponenten müssen beobachtbar sein.

Mindestens:

-   strukturierte Logs
    
-   Correlation IDs
    
-   Execution IDs
    
-   Node Execution IDs
    
-   Fehlerklassifikation
    
-   Laufzeiten
    
-   relevante Metriken
    

Execution Logs müssen einer Execution eindeutig zugeordnet werden können.

Node Logs müssen Node Execution und Execution zuordenbar machen.

Keine sensiblen Daten in Observability-Daten.

----------

# 35. HTTP Node

Der HTTP Node benötigt explizite Security-Regeln.

Besonders:

-   SSRF-Schutz
    
-   URL-Validierung
    
-   erlaubte/gesperrte Protokolle
    
-   Redirect-Verhalten
    
-   Timeout
    
-   Response Size Limit
    
-   Connection Limits
    
-   Header Handling
    
-   Fehlerbehandlung
    

Interne Netzwerkziele dürfen nicht einfach erreichbar sein, nur weil ein Benutzer eine URL konfigurieren kann.

----------

# 36. Web Search Node

Der Web Search Node muss:

-   Timeout besitzen
    
-   Fehler klassifizieren
    
-   Response-Größen begrenzen
    
-   strukturierte Ergebnisse liefern
    
-   externe Fehler sauber behandeln
    

Search Results müssen als Daten behandelt werden und dürfen nicht ungefiltert als vertrauenswürdige Systeminstruktionen in LLM-Prompts gelangen.

----------

# 37. SMTP Node

Der SMTP Node muss:

-   Credentials über Credential References beziehen
    
-   Timeouts verwenden
    
-   Fehler klassifizieren
    
-   keine Credentials loggen
    
-   keine SMTP-Verbindung unnötig offen halten
    

Tests verwenden einen kontrollierten lokalen SMTP-Testdienst.

Keine realen externen Mailversände in normalen Tests.

----------

# 38. Resource Limits

Resource Limits sind Backend-Verantwortung.

Mindestens relevant:

-   Execution Duration
    
-   Node Duration
    
-   Loop Iterations
    
-   Execution Depth
    
-   Parallelism
    
-   Output Size
    
-   Artifact Size
    
-   HTTP Response Size
    
-   LLM Token Usage
    
-   Retry Count
    

Limits müssen:

-   konfigurierbar
    
-   validierbar
    
-   beobachtbar
    
-   testbar
    

sein.

----------

# 39. Retry

Retry darf nicht pauschal auf alle Fehler angewendet werden.

Fehler müssen klassifiziert werden, beispielsweise:

```text
Transient
Permanent
Validation
Configuration
Provider
Timeout
Cancellation
```

Retry benötigt:

-   maximale Versuche
    
-   Backoff
    
-   klare Zustandsübergänge
    
-   Observability
    

Cancellation darf nicht durch Retry wiederbelebt werden.

----------

# 40. Timeout

Timeouts müssen auf den relevanten Ebenen kontrolliert werden:

```text
Execution
  └── Node
       └── External Operation
```

Ein Timeout darf nicht dazu führen, dass ein bereits abgebrochener Vorgang unkontrolliert weiterläuft.

Timeouts müssen im State und in Observability nachvollziehbar sein.

----------

# 41. Cancellation

Cancellation ist grundsätzlich kooperativ.

Nodes müssen Cancellation berücksichtigen, insbesondere bei:

-   langen Schleifen
    
-   externen Requests
    
-   AI-Aufrufen
    
-   Delay
    
-   Queue-/Worker-Operationen
    

Cancellation darf nicht nur eine Datenbankflag sein, die der Runtime unbekannt bleibt.

----------

# 42. Queue und Worker

Wenn Execution-Ausführung aus HTTP-Prozessen ausgelagert wird, müssen Worker und Queue sauber getrennt sein.

Grundmodell:

```text
API
 ↓
Execution Service
 ↓
Queue
 ↓
Worker
 ↓
Execution Runtime
```

HTTP Requests dürfen nicht für beliebig lange Executions blockiert werden.

Worker benötigen:

-   Heartbeat
    
-   eindeutige Job IDs
    
-   saubere Shutdown-Logik
    
-   Recovery-Verhalten
    
-   Fehlerzustände
    
-   Observability
    

----------

# 43. Recovery

Das Backend muss mit folgenden Situationen umgehen können:

-   Prozessabsturz
    
-   Worker-Absturz
    
-   Netzwerkfehler
    
-   Datenbankverbindungsabbruch
    
-   Redis-Ausfall
    
-   Provider Timeout
    
-   Node Timeout
    
-   teilweise ausgeführte Workflows
    

Recovery darf nicht auf einem simplen "alles erneut starten" basieren.

Es muss klar sein:

-   welcher State persistiert wurde
    
-   welcher Node ausgeführt wurde
    
-   welcher Node sicher erneut ausgeführt werden darf
    
-   welche Seiteneffekte bereits passiert sein können
    

----------

# 44. Idempotenz

Bei Retries und Recovery muss Idempotenz berücksichtigt werden.

Besonders kritisch:

-   E-Mail-Versand
    
-   Webhooks
    
-   externe API Calls
    
-   Plugin-Operationen
    
-   Queue Jobs
    
-   Workflow-Importe
    

Wenn echte Idempotenz nicht möglich ist, muss das Verhalten explizit dokumentiert und getestet werden.

----------

# 45. CLI

Die CLI darf Business Logic nicht duplizieren.

Sie verwendet dieselben Application Services wie die API.

Beispiel:

```text
CLI
 ↓
Application Service
 ↓
Domain
 ↓
Infrastructure
```

Nicht:

```text
CLI
 ↓
eigene zweite Business-Logik
```

----------

# 46. Import / Export

Workflowpakete dürfen niemals Secrets enthalten.

Ein Package enthält beispielsweise:

```text
manifest
metadata
graph
node configuration
prompts/templates
dependencies
resources
documentation
```

Nicht:

```text
API keys
passwords
session tokens
private credentials
```

Import muss validieren:

-   Schema-Version
    
-   Engine-Kompatibilität
    
-   Node-Verfügbarkeit
    
-   Plugin-Abhängigkeiten
    
-   Config
    
-   Graph
    
-   Ressourcen
    
-   Sicherheitsregeln
    

Round-Trip-Tests sind verpflichtend:

```text
Workflow
 ↓ export
Package
 ↓ import
Workflow
```

----------

# 47. Reports

Reports werden aus persistierten Daten erzeugt.

Ein Report darf nicht von flüchtigem Runtime-Memory abhängig sein.

Reports müssen secret-safe sein.

Mindestens relevante Informationen:

-   Workflow
    
-   Version
    
-   Execution
    
-   Start/Ende
    
-   Status
    
-   Node-Ausführungen
    
-   Fehler
    
-   Laufzeiten
    
-   relevante Metriken
    
-   Token Usage / Kosteninformationen, sofern vorhanden
    

----------

# 48. Authentication und Authorization

V1 verwendet lokale Authentifizierung.

Kein Enterprise SSO in V1.

Keine unnötig komplexe RBAC-Implementierung.

Die Architektur muss jedoch so gestaltet sein, dass spätere Berechtigungsmodelle möglich sind.

Jeder geschützte Resource-Zugriff muss serverseitig autorisiert werden.

Frontend-Sichtbarkeit ist keine Autorisierung.

----------

# 49. Security

Backend Security ist Bestandteil jeder Feature-Implementierung.

Besonders zu prüfen:

-   Authentication
    
-   Authorization
    
-   Secrets
    
-   SSRF
    
-   Path Traversal
    
-   Injection
    
-   Expression Evaluation
    
-   Plugin Loading
    
-   File Access
    
-   Webhooks
    
-   Resource Exhaustion
    
-   Request Limits
    
-   Input Validation
    
-   Error Disclosure
    
-   Dependency Security
    

Security Tests sind echte Tests und keine Checklisten.

----------

# 50. File-System-Zugriff

Dateipfade aus Benutzerinput dürfen niemals ungeprüft verwendet werden.

Zu verhindern:

```text
../../etc/passwd
```

sowie äquivalente Varianten über:

-   Encoding
    
-   Symlinks
    
-   absolute Pfade
    
-   alternative Separatoren
    

File Access muss auf definierte Verzeichnisse bzw. Ressourcen begrenzt sein.

----------

# 51. Logging

Logs müssen strukturiert sein.

Bevorzugt:

```json
{
  "level": "INFO",
  "event": "node_execution_completed",
  "execution_id": "...",
  "node_execution_id": "...",
  "node_id": "...",
  "duration_ms": 1234
}
```

Keine unstrukturierten Debug-Ausgaben in Production-Code.

Keine:

```python
print(secret)
```

Keine sensiblen Request-/Response-Dumps ohne explizite Redaction.

----------

# 52. Testing

Backend-Features benötigen Tests auf der passenden Ebene.

Mindestens relevant:

```text
Unit Tests
Component Tests
Integration Tests
API Tests
Contract Tests
Infrastructure Tests
Execution Tests
Security Tests
E2E Tests
```

Nicht jeder Test muss jede Schicht abdecken.

Aber kritische Infrastruktur darf nicht nur gemockt werden.

----------

# 53. Testcontainers

Für reale Infrastrukturtests sind Testcontainers zu verwenden, insbesondere für:

-   PostgreSQL
    
-   Redis
    
-   weitere relevante Infrastruktur
    

Die Tests müssen Container automatisch:

1.  starten
    
2.  konfigurieren
    
3.  verwenden
    
4.  bereinigen
    

Keine manuelle lokale Infrastruktur als Voraussetzung für Tests.

Keine globale Shared-Testdatenbank.

Keine Tests, die zufällig auf einem lokal laufenden PostgreSQL funktionieren.

----------

# 54. Datenbank-Integrationstests

Mindestens testen:

-   Migrationen
    
-   Repository-Verhalten
    
-   Constraints
    
-   Transactions
    
-   Concurrent Access, wo relevant
    
-   Rollback
    
-   Unique Constraints
    
-   Foreign Keys
    
-   State Persistence
    

Ein Test, der nur mit einem Mock funktioniert, beweist nicht, dass PostgreSQL korrekt funktioniert.

----------

# 55. Redis-Tests

Wenn Redis verwendet wird, müssen Redis-spezifische Funktionen gegen eine echte Redis-Instanz getestet werden.

Insbesondere:

-   Queue
    
-   Locking
    
-   Expiration
    
-   Heartbeats
    
-   Recovery
    
-   Duplicate Jobs
    

Mocks dürfen ergänzend verwendet werden, aber nicht als alleiniger Nachweis der Infrastrukturkorrektheit.

----------

# 56. API Tests

API Tests müssen den echten Application Stack soweit sinnvoll verwenden.

Zu testen:

-   Request Validation
    
-   Authentication
    
-   Authorization
    
-   Application Service Integration
    
-   Persistence
    
-   Error Mapping
    
-   Response Schema
    
-   Status Codes
    
-   Concurrency-relevante Fälle
    

API Tests dürfen nicht nur Router-Funktionen isoliert testen.

----------

# 57. Execution Tests

Execution Runtime Tests sind besonders wichtig.

Mindestens:

### Linear

```text
A → B → C
```

### Branch

```text
      → B
A ────
      → C
```

### Merge

```text
A → B ─┐
       ├→ D
A → C ─┘
```

### Parallel

Mehrere unabhängige Nodes müssen tatsächlich parallelisierbar sein, sofern die Runtime dies unterstützt.

### Failure

Ein Node schlägt fehl und die Execution verhält sich entsprechend der definierten Fehlerstrategie.

### Retry

Transient Error → Retry → Success.

### Timeout

Node überschreitet Timeout.

### Cancellation

Laufende Execution wird abgebrochen.

### Recovery

Prozess-/Worker-Abbruch wird realistisch simuliert.

----------

# 58. Node Contract Tests

Jeder Node muss seinen Contract erfüllen.

Tests müssen mindestens prüfen:

-   Metadata
    
-   Inputs
    
-   Outputs
    
-   Config
    
-   Validation
    
-   Runtime Result
    
-   Errors
    
-   Version
    

Built-in Nodes müssen standardisierte Contract Tests verwenden, soweit möglich.

----------

# 59. Provider Contract Tests

Provider Adapter müssen gegen ein gemeinsames Provider Contract getestet werden.

Beispielsweise:

```text
request
response
streaming
tool calls
token usage
provider errors
timeouts
```

Provider-spezifische Besonderheiten müssen explizit behandelt werden.

----------

# 60. Testdaten

Testdaten müssen:

-   deterministisch
    
-   klein
    
-   verständlich
    
-   isoliert
    

sein.

Keine Abhängigkeit von Produktionsdaten.

Keine echten Credentials.

Keine echten Kundendaten.

Keine zufälligen Zeitabhängigkeiten, sofern nicht explizit getestet.

Zeitabhängige Logik soll eine kontrollierbare Clock verwenden, wenn dies für Determinismus erforderlich ist.

----------

# 61. Keine Fake-Tests

Nicht akzeptabel:

```python
assert result is not None
```

als alleiniger Test einer komplexen Funktion.

Nicht akzeptabel:

```python
mock.execute.return_value = "success"
assert mock.execute.called
```

wenn dadurch die eigentliche Runtime nicht getestet wird.

Tests müssen die tatsächliche fachliche Wirkung prüfen.

----------

# 62. Keine Sleep-basierten Synchronisationstests

Nicht verwenden:

```python
await asyncio.sleep(5)
```

um auf irgendeinen Zustand zu warten.

Stattdessen:

-   deterministische Synchronisation
    
-   Events
    
-   Polling mit begrenztem Timeout
    
-   explizite Test-Hooks
    
-   Awaitable Conditions
    

Ein Test darf nicht aufgrund von Timing zufällig grün oder rot sein.

----------

# 63. Determinismus

Tests müssen möglichst deterministisch sein.

Insbesondere:

-   Zeit
    
-   Randomness
    
-   IDs
    
-   Provider Responses
    
-   Queue Scheduling
    
-   Parallel Execution
    

müssen bei Bedarf kontrolliert werden.

Parallelitätsprobleme sollen gezielt getestet werden und nicht zufällig auftreten.

----------

# 64. End-to-End Backend Journey

Mindestens ein vollständiger Backend-E2E-Test muss den zentralen Produktfluss abdecken:

```text
Create Workflow
    ↓
Create / Update Draft
    ↓
Add Nodes
    ↓
Configure Nodes
    ↓
Validate
    ↓
Publish
    ↓
Create Trigger
    ↓
Start Execution
    ↓
Execute Graph
    ↓
Persist State
    ↓
Observe Events / Logs / Metrics
    ↓
Inspect Execution
    ↓
Generate Report
    ↓
Export Workflow
    ↓
Import Workflow
    ↓
Execute Imported Workflow
```

Dieser Test muss reale Backend-Komponenten soweit sinnvoll integrieren.

----------

# 65. Configuration

Konfiguration erfolgt über klar definierte Settings.

Keine unkontrollierten:

```python
os.getenv(...)
```

an beliebigen Stellen im Code.

Environment Variables werden zentral geladen und validiert.

Fehlende kritische Konfiguration muss beim Startup oder beim relevanten Use Case eindeutig erkannt werden.

Secrets dürfen nicht in Logs erscheinen.

----------

# 66. Startup und Shutdown

Backend Startup muss deterministisch sein.

Startup kann unter anderem:

-   Configuration validieren
    
-   Dependencies initialisieren
    
-   Registry laden
    
-   Plugins laden
    
-   Datenbankverbindung prüfen
    

Shutdown muss:

-   laufende Ressourcen sauber freigeben
    
-   Worker kontrolliert stoppen
    
-   Connections schließen
    
-   keine neuen Jobs mehr annehmen
    

----------

# 67. Health und Readiness

Mindestens unterscheiden:

```text
Health
Readiness
```

Health bedeutet:

```text
Prozess lebt
```

Readiness bedeutet:

```text
Backend kann seine relevanten Abhängigkeiten verwenden
```

Readiness darf nicht pauschal "HTTP Server läuft" bedeuten.

----------

# 68. Performance

Performance-Optimierungen müssen messbar sein.

Keine premature abstractions oder Caches ohne konkreten Bedarf.

Zu beobachten:

-   DB Queries
    
-   N+1 Queries
    
-   Graph Traversal
    
-   Node Scheduling
    
-   Queue Latency
    
-   API Response Time
    
-   Provider Latency
    
-   Memory Usage
    

Performance Tests sind insbesondere für:

-   große Graphen
    
-   viele Node Executions
    
-   parallele Executions
    
-   große Outputs
    

relevant.

----------

# 69. Concurrency

Concurrency muss bewusst behandelt werden.

Kritische Bereiche:

-   Workflow Draft Updates
    
-   Publish
    
-   Execution State
    
-   Node Execution State
    
-   Queue Jobs
    
-   Worker Ownership
    
-   Credentials
    
-   Plugin Installation
    

Race Conditions dürfen nicht durch zufälliges Timing verdeckt werden.

Bei konkurrierenden Änderungen sind geeignete Mechanismen zu verwenden:

-   Optimistic Locking
    
-   Database Constraints
    
-   Transactions
    
-   Locks
    
-   Idempotency Keys
    

je nach Use Case.

----------

# 70. API Idempotency

Operationen, die bei Retries doppelte Seiteneffekte verursachen könnten, benötigen gegebenenfalls Idempotency-Unterstützung.

Besonders relevant:

-   Execution Start
    
-   Webhooks
    
-   Import
    
-   externe Aktionen
    
-   Queue Submission
    

Idempotency muss auf Use-Case-Ebene definiert werden.

----------

# 71. Dependency Injection

Dependencies müssen zentral und nachvollziehbar aufgebaut werden.

Keine versteckten globalen Service-Instanzen.

Keine fachlichen Singletons, wenn Execution-/Request-spezifischer State beteiligt ist.

Dependency Injection soll Tests ermöglichen, ohne die Produktionsarchitektur zu verfälschen.

----------

# 72. External Services

Externe Services werden hinter geeigneten Abstraktionen gekapselt.

Beispiele:

```text
LLM Provider
HTTP Client
SMTP Client
Web Search
Redis
Object Storage
```

Application-/Domain-Code darf nicht direkt von einem konkreten externen SDK abhängig sein, sofern eine Provider-/Port-Abstraktion vorgesehen ist.

----------

# 73. Timeouts für externe Services

Jeder externe I/O-Pfad benötigt bewusst definierte Timeouts.

Kein unbeschränkter:

```python
await client.get(...)
```

in produktivem Code.

Timeouts müssen testbar und beobachtbar sein.

----------

# 74. Netzwerkfehler

Netzwerkfehler müssen klassifiziert werden.

Beispielsweise:

```text
Connection Error
DNS Error
Timeout
TLS Error
HTTP 4xx
HTTP 5xx
Rate Limit
Provider Error
```

Nicht jeder HTTP-Fehler ist ein Retry-Fall.

----------

# 75. Dependency Updates

Dependencies dürfen nicht unkontrolliert aktualisiert werden.

Bei Updates berücksichtigen:

-   API-Kompatibilität
    
-   Security
    
-   Lizenz
    
-   Performance
    
-   Migrationen
    
-   Tests
    

Lockfile muss konsistent bleiben.

----------

# 76. Code Quality

Bevor eine Phase als abgeschlossen gilt:

-   Formatter erfolgreich
    
-   Linter erfolgreich
    
-   Type Checker erfolgreich
    
-   relevante Tests erfolgreich
    
-   Integrations-/Infrastrukturtests erfolgreich, sofern relevant
    
-   keine temporären Debug-Ausgaben
    
-   keine ungenutzten Imports
    
-   keine TODOs, die für die aktuelle Phase kritisch sind
    
-   keine bekannten Regressionen
    

----------

# 77. Dokumentation

Architekturentscheidungen müssen in den vorgesehenen Dokumenten landen.

Code-Kommentare erklären primär:

```text
Warum?
```

nicht:

```text
Was macht diese Zeile?
```

Öffentliche API- oder SDK-Verhaltensänderungen müssen dokumentiert werden.

----------

# 78. Roadmap-Disziplin

Das Backend wird phasenweise entwickelt.

Wenn eine Phase bearbeitet wird:

1.  nur den Scope dieser Phase implementieren
    
2.  bestehende Architektur berücksichtigen
    
3.  notwendige Tests implementieren
    
4.  notwendige Dokumentation aktualisieren
    
5.  keine Features aus zukünftigen Phasen vorziehen
    
6.  keine "vorsorgliche" halbe Implementierung zukünftiger Systeme erstellen
    

Wenn eine spätere Phase für die aktuelle Phase technisch zwingend erforderlich erscheint, muss die Abhängigkeit explizit dokumentiert werden.

Nicht einfach Scope erweitern.

----------

# 79. Änderungen an Architekturgrenzen

Architekturänderungen sind zulässig, wenn sie nachweislich besser sind.

Dann müssen:

-   `/docs/architecture.md`
    
-   betroffene Roadmaps
    
-   betroffene AGENTS-Dokumente
    

konsistent aktualisiert werden.

Keine stille Architekturabweichung.

----------

# 80. Phase Completion

Eine Backend-Phase ist erst abgeschlossen, wenn:

-   Scope vollständig implementiert
    
-   Tests vorhanden
    
-   relevante Integrationstests erfolgreich
    
-   Formatter erfolgreich
    
-   Linter erfolgreich
    
-   Type Checker erfolgreich
    
-   bestehende Tests weiterhin erfolgreich
    
-   Security-Auswirkungen geprüft
    
-   Architektur konsistent
    
-   Dokumentation aktualisiert
    
-   keine bekannte kritische Regression vorhanden
    

Danach:

```text
STOP
```

Keine Implementierung der nächsten Phase in derselben Session.

----------

# 81. Pflichtprüfungen vor Abschluss

Mindestens die für das Projekt verfügbaren Checks ausführen.

Beispielsweise:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
```

Falls das Projekt andere definierte Commands besitzt, gelten diese.

Für Infrastruktur-relevante Phasen zusätzlich:

```text
PostgreSQL Testcontainer
Redis Testcontainer
Migration Tests
Integration Tests
```

Für API-/Runtime-Phasen zusätzlich die relevanten API-/Execution-/E2E-Tests.

----------

# 82. Definition of Done für Backend-Code

Backend-Code ist nicht fertig, wenn er nur funktioniert.

Er ist fertig, wenn er:

```text
fachlich korrekt
→ architektonisch korrekt platziert
→ typisiert
→ validiert
→ getestet
→ beobachtbar
→ fehlertolerant
→ sicher
→ reproduzierbar
→ dokumentiert
```

ist.

Der zentrale Maßstab lautet:

> Backend-Code muss auch unter Fehlern, Retries, Neustarts, konkurrierenden Zugriffen und realer Infrastruktur nachvollziehbar und korrekt bleiben.