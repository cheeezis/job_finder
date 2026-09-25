"""Cost guard: asks before every model or tool call whether the agent may go on.

Two kinds of stops: JobLimitReached ends only the current job, the agent
continues with the next one. AgentStopped ends the agent for this run: it is
off, the model has no price, the ledger cannot be read or written, or the
daily or monthly money is used up. Every doubt counts as a stop.

Paid web searches have their own budget per job. It does not stop the job:
once it is used up, the agent keeps working without the search tool. One
model response can send several searches, so the searches of that last
response may exceed the budget; the money limits still apply.
"""

from decimal import Decimal

from job_finder.agent.pricing import PRICES, Usage, call_cost
from job_finder.persistence.agent_usage import record_model_call, spent_today_and_this_month


def euro(value, digits=2):
    """Write an amount the German way, as the log and the review show these messages."""
    return f"{value:.{digits}f} €".replace(".", ",")


class LimitReached(Exception):
    pass


class JobLimitReached(LimitReached):
    pass


class AgentStopped(LimitReached):
    pass


class CostGuard:
    def __init__(self, settings, model):
        self.settings = settings
        self.limits = settings.limits
        self.model = model
        self.job_id = None

    def start_job(self, job_id):
        """Reset the per-job counters; refuse to start when the run must stop."""
        self.check_run()
        self.job_id = job_id
        self.model_calls = 0
        self.tool_calls = 0
        self.web_searches = 0
        self.job_cost = Decimal(0)

    def check_run(self):
        if not self.settings.enabled:
            raise AgentStopped(f"Agent aus: {self.settings.reason}")
        if self.model not in PRICES:
            raise AgentStopped(f"Kein Preis für Modell {self.model} hinterlegt")
        try:
            today, month = spent_today_and_this_month()
        except Exception as error:
            raise AgentStopped(f"Kostenbuch nicht lesbar ({type(error).__name__})") from error
        if today >= self.limits.daily_max_cost_eur:
            raise AgentStopped(
                f"Tagesgrenze erreicht: {euro(today)} von {euro(self.limits.daily_max_cost_eur)}"
            )
        if month >= self.limits.monthly_max_cost_eur:
            raise AgentStopped(
                f"Monatsgrenze erreicht: {euro(month)} von {euro(self.limits.monthly_max_cost_eur)}"
            )

    def before_model_call(self):
        """Count the call before it happens, so a failed call still uses up a slot."""
        if self.job_id is None:
            raise RuntimeError("start_job muss vor dem ersten Modellaufruf laufen")
        self.check_run()
        if self.model_calls >= self.limits.job_max_model_calls:
            raise JobLimitReached(
                f"Stelle abgebrochen: {self.limits.job_max_model_calls} Modellaufrufe erreicht"
            )
        if self.job_cost >= self.limits.job_max_cost_eur:
            raise JobLimitReached(
                f"Stelle abgebrochen: {euro(self.job_cost, 3)} von "
                f"{euro(self.limits.job_max_cost_eur)} verbraucht"
            )
        self.model_calls += 1

    def after_model_call(self, usage):
        """Book the real cost; without usable token counts book the job maximum and stop it."""
        try:
            cost = call_cost(self.model, usage)
        except (AttributeError, ValueError) as error:
            cost = self.limits.job_max_cost_eur
            self.book(Usage(0, 0, 0), cost)
            raise JobLimitReached(
                "Stelle abgebrochen: Token-Angaben fehlen oder sind ungültig, "
                f"vorsichtshalber {euro(cost)} gebucht"
            ) from error
        self.book(usage, cost)

    def book(self, usage, cost):
        try:
            record_model_call(self.job_id, self.model, usage, cost)
        except Exception as error:
            raise AgentStopped(f"Kostenbuch nicht beschreibbar ({type(error).__name__})") from error
        self.job_cost += cost
        self.web_searches += usage.web_searches

    def search_allowed(self):
        """Whether the next model call may still offer the paid web search."""
        return self.web_searches < self.limits.job_max_web_searches

    def before_tool_call(self):
        if self.tool_calls >= self.limits.job_max_tool_calls:
            raise JobLimitReached(
                f"Stelle abgebrochen: {self.limits.job_max_tool_calls} Werkzeugaufrufe erreicht"
            )
        self.tool_calls += 1
