"""Composition of the domain layer (no transport, no drivers)."""
from __future__ import annotations

from ..core.clock import Clock
from ..infra.config import Settings
from ..infra.db import Database
from ..infra.events import EventBus
from ..infra.repos import Repos
from .inbox import InboxService
from .agents import AgentService
from .approvals import ApprovalService
from .company import CompanyService
from .memory import MemoryService
from .plan import PlanService
from .projects import ProjectService
from .sessions import SessionService
from .skills import SkillService
from .tasks import TaskService
from .workflows import WorkflowService


class Services:
    def __init__(self, settings: Settings, db: Database | None = None, clock: Clock | None = None):
        self.settings = settings
        self.clock = clock or Clock()
        if db is None:
            from .vault import apply_pending_restore
            apply_pending_restore(settings)         # "restore this backup" was chosen before the restart: it is put in place before the database opens
        self.db = db or Database(settings.db_path)
        self.db.migrate()
        self.repos = Repos(self.db)
        self.bus = EventBus(self.db, self.clock)
        base = (self.repos, self.bus, self.clock, settings)
        self.projects = ProjectService(*base)
        self.sessions = SessionService(*base, projects=self.projects)
        self.inbox = InboxService(*base, projects=self.projects)
        self.tasks = TaskService(*base, projects=self.projects, inbox=self.inbox)
        self.plan = PlanService(*base)
        self.memory = MemoryService(*base, projects=self.projects, inbox=self.inbox, tasks=self.tasks)
        self.agents = AgentService(*base, projects=self.projects, inbox=self.inbox, tasks=self.tasks, sessions=self.sessions)
        self.memory.agents = self.agents        # a chat's first packet includes the agent's identity and its own memory
        self.tasks.agents = self.agents         # a task that was sent back is reported again with the lesson its owner learned
        from .quality import QualityService
        self.quality = QualityService(*base, projects=self.projects, inbox=self.inbox, tasks=self.tasks)
        self.tasks.quality = self.quality       # the review gates and the points record
        from .rooms import DecisionRooms
        self.rooms = DecisionRooms(*base, projects=self.projects, inbox=self.inbox, quality=self.quality, agents=self.agents)
        from .limits import Limits
        self.limits = Limits(self.repos, self.bus, self.clock, settings, agents=self.agents)     # modes, usage limits and what replaces a way that is at its limit
        self.agents.backfill()
        self.bus.subscribe("project.created", lambda e: self.agents.backfill())      # a new project's Master gets its name at once
        self.projects.backfill_folders()
        self.approvals = ApprovalService(*base, inbox=self.inbox)
        self.company = CompanyService(*base, projects=self.projects, inbox=self.inbox, tasks=self.tasks, agents=self.agents, plan=self.plan,
                                      memory=self.memory)
        self.company.backfill()
        self.workflows = WorkflowService(*base, projects=self.projects, inbox=self.inbox, tasks=self.tasks, company=self.company,
                                          approvals=self.approvals)
        from .vault import Vault
        self.vault = Vault(*base, projects=self.projects, sessions=self.sessions)      # delete with a way back, and backups of everything
        from .maintenance import MaintenanceService
        self.maintenance = MaintenanceService(*base, projects=self.projects, inbox=self.inbox, tasks=self.tasks, agents=self.agents, company=self.company)
        self.skills = SkillService(*base, projects=self.projects, inbox=self.inbox)
        self.memory.skills = self.skills
        self.memory.company = self.company      # a chat's first packet includes the owner's company knowledge
