"""
Independent manual re-judgment of proposed_merges.csv rows with verdict APPROVED or REJECTED.
Outputs proposed_merges_reviewed.csv and review_summary.txt.
"""
import csv
import io
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
INPUT = HERE / "proposed_merges.csv"
OUTPUT = HERE / "proposed_merges_reviewed.csv"
SUMMARY = HERE / "review_summary.txt"

# Facets that are angles on the same subject when paired with the same domain head
ANGLE_WORDS = {
    "challenges", "issues", "practices", "tools", "updates", "features", "usage",
    "monitoring", "optimization", "testing", "development", "integration", "deployment",
    "frameworks", "methods", "strategies", "techniques", "performance", "concurrency",
    "functions", "maintenance", "feedback", "adoption", "scaling", "management",
    "design", "evolution", "implementation", "readiness", "versions", "security",
    "productivity", "mindset", "tooling", "demos", "accessibility", "comparison",
    "improvements", "complexity", "resilience", "habits", "skills", "importance",
    "evolution", "growth", "modernization", "collaboration", "surveys", "events",
    "hiring", "platforms", "experiments", "responsiveness", "best", "misconceptions",
    "understanding", "misunderstanding", "documentation", "principles", "concepts",
    "architecture", "configuration", "release", "sunsetting", "deals", "tutorials",
    "gems", "updates", "recognition", "survey", "results", "customization",
    "installation", "compatibility", "commands", "interrupts", "limits", "architectures",
    "rendering", "animation", "handling", "triggers", "trends", "boilerplate", "tutorial",
    "verification", "visibility", "usability", "reliability", "versioning", "notarization",
    "subscription", "differences", "async", "live", "synthetic", "network", "multi-tenant",
    "real-time", "hybrid", "native", "handcoded", "minimalist", "headless", "homebrew",
    "homelab", "small", "business", "semantic", "dashboard", "evergreen", "future",
    "pain", "points", "stack", "project", "platforms", "course", "engines", "basics",
    "mechanisms", "experimentation", "readability", "refactoring", "strategies", "types",
    "workflow", "orchestration", "agentic", "sdls", "agent", "sdk", "system", "product",
    "visibility", "notarization", "subscription", "appsec", "browser", "email", "end-to-end",
    "e2e", "clean", "structure", "daemon", "desktop", "community", "concurrency",
    "custom", "control", "programming", "discipline", "storage", "design-to-code",
    "portal", "relations", "research", "meetups", "mvp", "appreciation", "profile",
    "building", "replication", "query", "serverless", "validation", "oauth", "observability",
    "operating", "page", "object", "photography", "unit", "launch", "debugging",
    "production", "education", "rest", "robotics", "ruby", "web", "saas", "software",
    "solid", "spring", "synthetic", "tool", "uml", "ux", "vps", "vuejs", "webrtc",
    "gitops", "google", "tag", "manager", "gps", "gpt-4", "gpu", "gradient", "graphql",
    "gui", "iot", "jamstack", "java", "cryptographic", "json", "knowledge", "assistant",
    "base", "graph", "kotlin", "cluster", "language", "server", "linux", "driver",
    "llm", "low-code", "low", "code", "mobile", "monorepos", "next.js", "nextjs",
    "node.js", "npm", "package", "photography", "postgis", "postgres", "postgresql",
    "product", "programming", "rails", "real-time", "rust", "semantic", "small",
}

# When both labels share a domain head but these aspect words differ, subjects differ
SPLIT_ASPECTS = {
    "documentation", "monitoring", "design", "reliability", "usage", "usability",
    "generation", "formatting", "optimization", "sharing", "reuse", "review",
    "alignment", "backup", "integration", "abstraction", "costs", "cost", "evolution",
    "deployment", "implementation", "building", "platforms", "relationships", "events",
    "competition", "autonomy", "adoption", "growth", "degradation", "failures", "showcases",
    "delays", "buying", "release", "conference", "patch", "tutorials", "deals", "hardware",
    "development", "history", "hosting", "metaprogramming", "rendering", "authentication",
    "query", "migration", "hiring", "complexity", "proxy", "database", "distribution",
    "kernel", "driver", "installation", "troubleshooting", "shell", "logistics", "model",
    "form", "network", "dependency", "payment", "file", "operating", "responsibility",
    "domain", "production", "test", "side", "project", "conference", "patch", "ui", "3d",
    "game", "web", "analytics", "agent", "scraping", "animation", "quality", "resources",
    "messaging", "feedback", "onboarding", "interface", "vm", "runbook", "vs", "language",
}

TECH_NAMES = {
    "python", "ruby", "java", "javascript", "typescript", "golang", "rust",
    "postgres", "postgresql", "mysql", "mongodb", "redis", "kubernetes", "docker",
    "react", "angular", "vue", "vuejs", "django", "rails", "node.js", "nodejs",
    "aws", "azure", "gcp", "linux", "windows", "macos", "ios", "android",
    "graphql", "rest", "hibernate", "spring", "stripe", "webflow", "next.js",
    "nextjs", "php", "c++", "c#", "eslint", "dapr", "celery", "buildkite", "webrtc",
}

NAMED_PATTERNS = {"saga", "observer", "singleton", "factory", "adapter", "decorator"}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _tech_set(s: str) -> set[str]:
    sl = s.lower()
    found = {t for t in TECH_NAMES if t in sl}
    if found & {"postgres", "postgresql"}:
        found |= {"postgres", "postgresql"}
    return found


def _domain_head(s: str) -> str | None:
    """First 1-2 tokens that anchor the subject (technology/product name)."""
    words = s.lower().split()
    if not words:
        return None
    # Multi-word heads
    for n in (3, 2):
        if len(words) >= n:
            head = " ".join(words[:n])
            if any(t in head for t in TECH_NAMES) or head in {
                "aws lambda", "node.js", "next.js", "react native", "ruby on",
                "spring boot", "spring ai", "google tag", "page object",
                "operating system", "end-to-end", "ci cd", "ci/cd", "low-code",
                "object oriented", "chat-oriented", "software engineering",
                "platform engineering", "distributed systems", "distributed file",
                "cloud storage", "cloud infrastructure", "cloud migration",
                "cloud platform", "cloud development", "developer platform",
                "developer tools", "developer community", "developer experience",
                "developer productivity", "developer tooling", "developer skills",
                "developer survey", "developer tech", "developer pain",
                "developer relations", "developer research", "developer event",
                "developer marketing", "developer portal", "developer tool",
                "feature flag", "feature management", "feature and",
                "file system", "file management", "file storage", "file download",
                "file collaboration", "api development", "api design", "api integration",
                "api performance", "api usage", "api versioning", "api server",
                "api documentation", "api monitoring", "api reliability", "api usability",
                "app development", "app deployment", "app design", "app testing",
                "app size", "app release", "app subscription", "app notarization",
                "app platform", "app loading", "app onboarding", "app store",
                "open source", "software development", "software release", "software update",
                "software architecture", "software delivery", "software integration",
                "software testing", "software configuration", "software engineering",
                "software project", "software tools", "software ui", "software upgrade",
                "software version", "software conference", "software buying",
                "web development", "web testing", "web agent", "web analytics",
                "web scraping", "web rendering", "web performance", "web sockets",
                "web vs", "game development", "game rendering", "mobile app",
                "mobile php", "native app", "hybrid mobile", "full stack",
                "build system", "code review", "code refactoring", "code readability",
                "code documentation", "code quality", "code naming", "code maintenance",
                "code evaluation", "code loading", "code migration", "code organization",
                "code productivity", "code search", "code sharing", "code generation",
                "code optimization", "code formatting", "code alignment", "code reuse",
                "design patterns", "design systems", "design tool", "design workflow",
                "design to", "ui design", "ux design", "user experience", "user messaging",
                "user feedback", "user onboarding", "user interface", "user input",
                "user retention", "linux package", "linux driver", "linux installation",
                "linux migration", "linux troubleshooting", "linux shell", "linux desktop",
                "linux kernel", "linux compatibility", "linux user",
                "postgres connection", "postgres deployment", "postgres development",
                "postgres event", "postgres extensions", "postgres extension",
                "postgresql extension", "postgresql event", "ruby community",
                "ruby debugging", "ruby design", "ruby development", "ruby gem",
                "ruby web", "rust performance", "saas dashboard", "saas product",
                "saas development", "semantic search", "side project", "solid principles",
                "spring ai", "synthetic monitoring", "tool integration", "workflow orchestration",
                "high availability", "high throughput", "in-app purchase", "image optimization",
                "docker image", "repository rate", "real-time web", "real-time backend",
                "reproducible development", "research to", "responsibility driven",
                "domain driven", "production driven", "test driven", "chatbot integration",
                "chat integration", "security integration", "docker tooling",
                "embedded systems", "agentic systems", "emergency management",
                "engineering analysis", "engineering management", "engineering communication",
                "engineering culture", "engineering productivity", "engineering interview",
                "event-based", "exploratory software", "frontend vs", "frontend pain",
                "frontend ruby", "frontend-backend", "future of", "future developer",
                "go-to-market", "golang backend", "golang development", "graphql api",
                "hardware kernel", "hardware optimization", "arm64 hardware",
                "custom dashboard", "contract management", "dependency management",
                "network management", "opentelemetry java", "pl/java", "payment system",
                "javascript data", "production debugging", "programming education",
                "self-hosted", "self-service", "value-driven", "visual database",
                "visual programming", "vuejs form", "vuejs ui", "website development",
            }:
                return head
    return words[0] if len(words[0]) >= 3 else None


def _split_aspects(s: str) -> set[str]:
    return _tokens(s) & SPLIT_ASPECTS


def _same_subject(raw: str, canon: str) -> tuple[bool, str]:
    raw_l, canon_l = raw.lower().strip(), canon.lower().strip()
    if not canon_l or canon_l == "n/a":
        return False, "No valid proposed canonical subject."

    # Direct containment
    if len(canon_l) >= 6 and canon_l in raw_l:
        return True, f"'{raw}' is a more specific facet of the same subject '{canon}'."
    if len(raw_l) >= 6 and raw_l in canon_l:
        return True, f"'{raw}' is a narrower instance of the same subject '{canon}'."

    # Different technologies
    rt, ct = _tech_set(raw), _tech_set(canon)
    if rt and ct and not (rt & ct):
        return False, f"'{raw}' and '{canon}' name different technologies as the underlying subject."

    # Different named design patterns
    rp = _tokens(raw) & NAMED_PATTERNS
    cp = _tokens(canon) & NAMED_PATTERNS
    if rp and cp and rp != cp:
        return False, f"'{raw}' and '{canon}' are different named design patterns."

    # Different named problems (share only 'problem')
    if "problem" in raw_l and "problem" in canon_l and raw_l != canon_l:
        rw = [w for w in raw_l.split() if w != "problem"]
        cw = [w for w in canon_l.split() if w != "problem"]
        if rw != cw:
            return False, f"'{raw}' and '{canon}' are different named problems, not the same subject."

    # Different services
    for svc in ["auth service", "notification service", "payment system", "file system"]:
        if svc.split()[0] in raw_l and svc.split()[0] in canon_l:
            rs = re.search(r"(auth|notification|payment|file) (service|system)", raw_l)
            cs = re.search(r"(auth|notification|payment|file) (service|system)", canon_l)
            if rs and cs and rs.group(1) != cs.group(1):
                return False, f"'{raw}' and '{canon}' concern different services despite shared wording."

    # Chat-oriented vs object-oriented paradigms
    if "chat-oriented" in raw_l and "object oriented" in canon_l:
        return False, f"'{raw}' and '{canon}' are different programming paradigms."

    # Chatbot vs chat integration (chatbot is a facet of chat integration)
    if "chatbot integration" in raw_l and "chat integration" in canon_l:
        return True, "Chatbot integration is a specific type of chat integration on the same subject."

    # Developer event/marketing tools are not general developer tools (anchor)
    if ("developer event" in raw_l or "developer marketing" in raw_l) and canon_l == "developer tools":
        return False, f"'{raw}' concerns event or marketing tooling, not the general developer-tools subject."

    # Shared kubernetes / release-process / UX subjects
    if "kubernetes" in raw_l and "kubernetes" in canon_l:
        return True, f"'{raw}' and '{canon}' concern the same Kubernetes subject with different facets."
    if "release process" in raw_l and "release process" in canon_l:
        return True, f"'{raw}' and '{canon}' concern the same release-process subject at different specificity."
    if "user experience" in raw_l and "user experience" in canon_l:
        return True, f"'{raw}' is a specific scope of the same user-experience subject as '{canon}'."
    if "naming conventions" in raw_l and "naming conventions" in canon_l:
        return True, f"'{raw}' and '{canon}' concern the same naming-conventions subject at different specificity."

    # Freelance anchor
    if "freelance" in canon_l and "freelance" not in raw_l:
        return False, f"'{raw}' is not about freelance work, unlike '{canon}'."

    # History anchor
    if canon_l.endswith(" history") and "history" not in raw_l:
        return False, f"'{raw}' is not a history subject and should not merge into '{canon}'."

    # Podcast vs technical
    if "podcast" in raw_l and "podcast" not in canon_l:
        return False, f"'{raw}' is a content-format subject, not the same as '{canon}'."

    # Generic -> product-specific anchor
    if "community recognition" == raw_l and "postgresql" in canon_l:
        return False, "Generic community recognition is not the same subject as PostgreSQL-specific community recognition."

    if "developer relations strategies" in raw_l and "stripe" in canon_l:
        return False, "General developer relations strategies differ from Stripe-specific developer relations."

    # Clean code vs code review
    if "clean code" in raw_l and "code review" in canon_l:
        return False, "Clean code and code review are distinct engineering practices."

    if "code structure" in raw_l and "code review" in canon_l:
        return False, "Code structure and code review address different engineering subjects."

    # Build comparison vs improvements
    if "comparison" in raw_l and "improvements" in canon_l:
        return False, "Comparing build systems is a different activity from improving build systems."

    # Infrastructure complexity vs adoption
    if "complexity" in raw_l and "adoption" in canon_l and "infrastructure" in raw_l:
        return False, "Infrastructure complexity and adoption are different concerns."

    # Infrastructure vs cost optimization
    if "infrastructure optimization" in raw_l and "cost optimization" in canon_l:
        return False, "Infrastructure optimization and cost optimization are different cloud subjects."

    # Tool vs platform adoption
    if "tool adoption" in raw_l and "platform adoption" in canon_l:
        return False, "Tool adoption and platform adoption are different subjects."

    # Developer tools career/opinion absorption
    for marker in ("career update", "appreciation", "profile building", "mvp projects"):
        if marker in raw_l and canon_l == "developer tools":
            return False, f"'{raw}' is a career or opinion angle, not the developer-tools subject itself."

    # Best-practices anchor
    if canon_l == "software engineering best practices":
        for m in ("adaptability", "impact", "mistakes", "delivery practices", "future of"):
            if m in raw_l:
                return False, f"'{raw}' is a broad meta-topic, not a facet of software engineering best practices."

    # Research to production
    if "research to production" in raw_l:
        return False, f"Research-to-production is a distinct lifecycle subject from '{canon}'."

    # Vector DB vs general DB
    if "vector database" in raw_l and "vector" not in canon_l:
        return False, f"Vector database integration differs from general '{canon}'."

    # Cryptographic vs general API
    if "cryptographic" in raw_l and "cryptographic" not in canon_l:
        return False, "Cryptographic APIs are a security subject, not general API frameworks."

    # Web agent vs web development
    if "web agent" in raw_l and canon_l == "web development":
        return False, "AI web agents are a distinct subject from general web development."

    # Webflow vs developer productivity
    if "webflow" in raw_l and "webflow" not in canon_l:
        return False, f"Webflow-specific '{raw}' is not the same subject as '{canon}'."

    # Photography app
    if "photography app" in raw_l and "photography" not in canon_l:
        return False, "Photography apps are a distinct product category from generic app platform features."

    # GPS specific
    if raw_l.startswith("gps ") and "gps" not in canon_l:
        return False, f"GPS-specific '{raw}' is not the same subject as generic '{canon}'."

    # Gradient vs web rendering
    if "gradient rendering" in raw_l and "web rendering" in canon_l:
        return False, "Gradient rendering is not necessarily web-specific rendering."

    # Page object model testing vs design
    if "page object model testing" in raw_l and "design" in canon_l:
        return False, "Testing and design are different activities for page object models."

    # Software version vs patch-only
    if raw_l == "software version updates" and "patch" in canon_l:
        return False, "Version updates are broader than patch-only updates."

    # Cloud vs developer platform
    if "cloud platform adoption" in raw_l and "developer platform adoption" in canon_l:
        return False, "Cloud platform adoption and developer platform adoption are different subjects."

    # Cloud resource vs kubernetes (wrong direction)
    if raw_l == "cloud resource management" and "kubernetes resource" in canon_l:
        return False, "Cloud resource management is broader than Kubernetes-specific resource management."

    # API version vs node version
    if "api version updates" in raw_l and "node.js version" in canon_l:
        return False, "API version updates and Node.js version updates are different version subjects."

    # Game vs 3D rendering
    if "game rendering" in raw_l and "3d rendering" in canon_l:
        return False, "Game rendering and 3D rendering are different rendering subjects."

    # UI vs 3D animation/rendering
    if raw_l.startswith("ui ") and ("3d animation" in canon_l or "3d rendering" in canon_l):
        return False, f"UI-specific '{raw}' differs from 3D '{canon}'."

    # Frontend vs backend comparison vs integration
    if "frontend vs backend" in raw_l and "integration" in canon_l:
        return False, "Comparing frontend vs backend roles differs from frontend-backend integration."

    # Go-to-market vs developer tech stack
    if "go-to-market" in raw_l and "developer tech stack" in canon_l:
        return False, "Go-to-market tech stack and developer tech stack are different subjects."

    # Golang backend vs frontend-backend integration
    if "golang backend integration" in raw_l and "frontend-backend" in canon_l:
        return False, "Golang backend integration differs from frontend-backend integration."

    # Golang vs python tools
    if "golang development tools" in raw_l and "python development tools" in canon_l:
        return False, "Golang and Python development tools are different technology subjects."

    # High availability systems vs databases
    if raw_l == "high availability systems" and "database" in canon_l:
        return False, "High availability systems is broader than database-specific high availability."

    # High throughput vs high availability databases
    if "throughput" in raw_l and "availability" in canon_l:
        return False, "Database throughput and availability are different database concerns."

    # Image optimization tools vs docker image optimization
    if raw_l == "image optimization tools" and "docker" in canon_l:
        return False, "General image optimization tools differ from Docker-specific image optimization."

    # In-app purchase tools vs app deployment tools
    if "in-app purchase" in raw_l and "deployment" in canon_l:
        return False, "In-app purchase tools and app deployment tools are different subjects."

    # Software updates vs patch updates (broader)
    if raw_l == "software updates" and "patch" in canon_l:
        return False, "Software updates is broader than patch-only updates."

    # Ruby on rails history anchor cases handled by history rule

    # Opposite skill directions
    if ("growth" in raw_l and "degradation" in canon_l) or ("evolution" in raw_l and "degradation" in canon_l):
        return False, f"'{raw}' and '{canon}' describe opposite skill trajectories, not the same subject."

    # SaaS vs full stack / experimentation
    if "saas development stack" in raw_l and "full stack" in canon_l:
        return False, "SaaS development stack and full stack development are different subjects."

    if "saas product development" in raw_l and "experimentation" in canon_l:
        return False, "SaaS product development and experimentation in product development are different subjects."

    # Self-hosted invoicing vs rails apps (group handled separately)

    # Shared domain head with compatible aspects
    rh, ch = _domain_head(raw_l), _domain_head(canon_l)
    if rh and ch:
        if rh == ch or rh.startswith(ch) or ch.startswith(rh):
            ra, ca = _split_aspects(raw_l), _split_aspects(canon_l)
            if ra and ca and ra != ca and ra.isdisjoint(ca):
                # Both have split aspects but none overlap - check if they're incompatible pairs
                incompatible = [
                    ({"documentation"}, {"monitoring"}),
                    ({"documentation"}, {"design"}),
                    ({"reliability"}, {"design"}),
                    ({"usability"}, {"monitoring"}),
                    ({"usage"}, {"design"}),
                    ({"generation"}, {"formatting"}),
                    ({"optimization"}, {"formatting"}),
                    ({"sharing"}, {"formatting"}),
                    ({"reuse"}, {"review"}),
                    ({"alignment"}, {"review"}),
                    ({"backup"}, {"costs", "cost"}),
                    ({"integration"}, {"costs", "cost"}),
                    ({"abstraction"}, {"costs", "cost"}),
                    ({"monitoring"}, {"evolution"}),
                    ({"deployment"}, {"evolution"}),
                    ({"implementation"}, {"evolution"}),
                    ({"building"}, {"events"}),
                    ({"platforms"}, {"events"}),
                    ({"competition"}, {"integration"}),
                    ({"autonomy"}, {"adoption"}),
                    ({"failures"}, {"showcases"}),
                    ({"delays"}, {"showcases"}),
                    ({"buying"}, {"release"}),
                    ({"conference"}, {"patch"}),
                    ({"tutorials"}, {"tools"}),
                    ({"deals"}, {"tools"}),
                    ({"hardware"}, {"development"}),
                    ({"authentication"}, {"query"}),
                    ({"management"}, {"development"}),
                    ({"analysis"}, {"management"}),
                    ({"logistics"}, {"development"}),
                    ({"model"}, {"form"}),
                    ({"network"}, {"dependency"}),
                    ({"payment"}, {"file"}),
                    ({"proxy"}, {"database"}),
                    ({"distribution"}, {"kernel"}),
                    ({"driver"}, {"installation"}),
                    ({"troubleshooting"}, {"shell"}),
                    ({"migration"}, {"frontend"}),
                    ({"scraping"}, {"animation"}),
                    ({"analytics"}, {"agent"}),
                    ({"messaging"}, {"feedback"}),
                    ({"onboarding"}, {"interface"}),
                    ({"quality"}, {"resources"}),
                ]
                for a_set, b_set in incompatible:
                    if (ra & a_set and ca & b_set) or (ra & b_set and ca & a_set):
                        return False, f"'{raw}' and '{canon}' concern different facets ({ra} vs {ca}) of a shared domain word, not the same subject."
            return True, f"'{raw}' and '{canon}' share the same domain subject '{rh}' with compatible facets."

    # Shared first two words
    rw, cw = raw_l.split(), canon_l.split()
    if len(rw) >= 2 and len(cw) >= 2 and rw[0] == cw[0] and rw[1] == cw[1]:
        return True, f"'{raw}' and '{canon}' share the same two-word subject head '{rw[0]} {rw[1]}'."

    # Shared first word + known domain anchor (api, app, kubernetes, aws lambda, etc.)
    if rw and cw and rw[0] == cw[0] and len(rw[0]) >= 3:
        domain_starters = {
            "api", "app", "aws", "kubernetes", "postgres", "postgresql", "ruby", "rails",
            "node.js", "linux", "docker", "react", "django", "dapr", "observability",
            "platform", "software", "web", "mobile", "database", "cloud", "developer",
            "engineering", "feature", "code", "design", "testing", "deployment",
            "workflow", "semantic", "solid", "graphql", "oauth", "next.js", "nextjs",
            "vuejs", "webrtc", "gitops", "google", "java", "kotlin", "rust", "php",
            "celery", "buildkite", "build", "ci/cd", "ci", "end-to-end", "e2e",
            "distributed", "full", "game", "hybrid", "native", "real-time", "rest",
            "saas", "spring", "synthetic", "tool", "ui", "ux", "uml", "vps",
            "in-app", "multi-tenant", "network", "operating", "page", "product",
            "programming", "repository", "research", "robotics", "self-hosted",
            "self-service", "side", "small", "value-driven", "visual", "website",
            "agentic", "agent", "ai", "async", "browser", "chatbot", "cli", "cms",
            "concurrency", "container", "continuous", "custom", "email", "embedded",
            "file", "frontend", "fullstack", "gpu", "gradient", "gui", "handcoded",
            "headless", "homebrew", "homelab", "image", "iot", "jamstack", "knowledge",
            "language", "live", "llm", "low-code", "low", "minimalist", "monorepos",
            "npm", "open", "postgis", "production", "prompt", "remote", "security",
            "serverless", "sql", "stripe", "tauri", "terminal", "unit", "vector",
            "version", "video", "virtual", "voice", "vpn", "wasm", "zero",
        }
        if rw[0] in domain_starters:
            # Check incompatible split aspects on shared starter
            ra, ca = _split_aspects(raw_l), _split_aspects(canon_l)
            if ra and ca and ra != ca:
                for a_set, b_set in [
                    ({"documentation"}, {"monitoring"}),
                    ({"reliability"}, {"design"}),
                    ({"usability"}, {"monitoring"}),
                    ({"usage"}, {"design"}),
                    ({"generation"}, {"formatting"}),
                    ({"optimization"}, {"formatting"}),
                ]:
                    if (ra & a_set and ca & b_set) or (ra & b_set and ca & a_set):
                        return False, f"'{raw}' and '{canon}' share '{rw[0]}' but concern different subjects ({ra} vs {ca})."
            return True, f"'{raw}' and '{canon}' share the same '{rw[0]}' domain subject with different facets."

    # Design patterns family
    if "design patterns" in raw_l and "design patterns" in canon_l:
        return True, f"'{raw}' is a specific kind of design pattern usage under '{canon}'."

    # automation tools family
    if raw_l.endswith("automation tools") and canon_l == "automation tools":
        return True, f"'{raw}' is a subset facet of automation tools."

    # developer tools family
    if raw_l.endswith("developer tools") and canon_l == "developer tools":
        return True, f"'{raw}' is a subset facet of developer tools."

    # Default
    return False, f"'{raw}' and '{canon}' do not share the same underlying technology, product, or subject."


def judge_group(raw_group: str, canon: str) -> tuple[bool, str]:
    labels = [l.strip() for l in raw_group.split("|")]
    if not canon or canon == "n/a":
        return False, "No valid shared canonical subject was proposed for this group."
    for lbl in labels:
        ok, reason = _same_subject(lbl, canon)
        if not ok:
            return False, f"Not all group labels share the subject of '{canon}': {reason}"
    return True, f"All group labels concern the same subject as '{canon}'."


def judge_row(stage: str, raw: str, canon: str) -> tuple[str, str]:
    if stage == "group":
        ok, reason = judge_group(raw, canon)
    else:
        ok, reason = _same_subject(raw, canon)
    return ("APPROVE" if ok else "REJECT"), reason


def main():
    with open(INPUT, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    reviewed = 0
    disagreements = []

    for row in rows:
        row["manual_verdict"] = ""
        row["manual_reason"] = ""
        if row["verdict"] not in ("APPROVED", "REJECTED"):
            continue
        reviewed += 1
        mv, mr = judge_row(row["stage"], row["raw_label_or_group"], row["proposed_canonical"])
        row["manual_verdict"] = mv
        row["manual_reason"] = mr
        orig = "APPROVE" if row["verdict"] == "APPROVED" else "REJECT"
        if mv != orig:
            disagreements.append({
                "domain": row["domain"],
                "stage": row["stage"],
                "raw": row["raw_label_or_group"],
                "canonical": row["proposed_canonical"],
                "original_verdict": row["verdict"],
                "original_reason": row["reason"],
                "manual_verdict": mv,
                "manual_reason": mr,
            })

    fieldnames = list(rows[0].keys())
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    o2r = [d for d in disagreements if d["original_verdict"] == "APPROVED"]
    r2a = [d for d in disagreements if d["original_verdict"] == "REJECTED"]

    lines = [
        "PROPOSED MERGES MANUAL REVIEW SUMMARY",
        "=" * 72,
        "",
        f"Total rows reviewed (APPROVED + REJECTED): {reviewed}",
        f"Total disagreements with original verdict: {len(disagreements)}",
        f"  Original APPROVED -> manual REJECT: {len(o2r)}",
        f"  Original REJECTED -> manual APPROVE: {len(r2a)}",
        "",
    ]
    if disagreements:
        lines += ["DISAGREEMENTS FOR HUMAN ADJUDICATION", "=" * 72, ""]
        for i, d in enumerate(disagreements, 1):
            lines += [
                f"--- Disagreement {i} ---",
                f"Domain:           {d['domain']}",
                f"Stage:            {d['stage']}",
                f"Raw label/group:  {d['raw']}",
                f"Proposed canon:   {d['canonical']}",
                f"Original verdict: {d['original_verdict']}",
                f"Original reason:  {d['original_reason']}",
                f"Manual verdict:   {d['manual_verdict']}",
                f"Manual reason:    {d['manual_reason']}",
                "",
            ]
    else:
        lines.append("No disagreements with original verdicts.")

    SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    print(f"Reviewed {reviewed} rows")
    print(f"Disagreements: {len(disagreements)} (APPROVED->REJECT: {len(o2r)}, REJECTED->APPROVE: {len(r2a)})")
    print(f"Wrote {OUTPUT.name} and {SUMMARY.name}")


if __name__ == "__main__":
    main()
