from abc import ABC, abstractmethod

class BaseTool(ABC):
    @property
    @abstractmethod
    def id(self) -> str:
        """Unique ID of the tool (e.g. 'fb_post_scraper')"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name of the tool in the CLI menu (e.g. 'Facebook Post Scraper')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Brief explanation of what the tool does"""
        pass

    @abstractmethod
    def run(self, config: dict, cookies: dict) -> None:
        """Run the tool's main functionality"""
        pass
