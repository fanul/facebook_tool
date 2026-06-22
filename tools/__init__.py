import os
import importlib
import inspect
from core.base_tool import BaseTool

def load_tools() -> list[BaseTool]:
    """
    Dynamically discover and load all tool plugins inheriting from BaseTool
    located inside the tools/ directory.
    """
    loaded_tools = []
    current_dir = os.path.dirname(os.path.abspath(__file__))

    # List all items in the tools/ directory
    for item in os.listdir(current_dir):
        item_path = os.path.join(current_dir, item)
        if os.path.isdir(item_path) and not item.startswith('__') and not item.startswith('.'):
            module_name = f"tools.{item}"

            # Try to load the module (preferring tools.<item>.scraper, fallback to tools.<item>)
            imported_module = None
            try:
                imported_module = importlib.import_module(f"{module_name}.scraper")
            except ImportError as e:
                # If the ImportError occurred in a nested module, print the traceback
                if e.name and e.name != f"{module_name}.scraper":
                    print(f"Error loading dependencies in {module_name}.scraper: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
                try:
                    imported_module = importlib.import_module(module_name)
                except ImportError:
                    continue

            if imported_module:
                # Find all classes that inherit from BaseTool and instantiate them
                for name, obj in inspect.getmembers(imported_module, inspect.isclass):
                    if issubclass(obj, BaseTool) and obj is not BaseTool:
                        try:
                            tool_instance = obj()
                            loaded_tools.append(tool_instance)
                        except Exception as e:
                            print(f"Error loading tool class '{name}' in {module_name}: {e}")

    return loaded_tools
