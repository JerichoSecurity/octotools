import os
import sys
import importlib
import inspect
import traceback
from typing import Dict, Any, List, Tuple
import time
class Initializer:
    def __init__(self, enabled_tools: List[str] = [], model_string: str = None, verbose: bool = False, vllm_config_path: str = None):
        self.toolbox_metadata = {}
        self.available_tools = []
        self.enabled_tools = enabled_tools
        self.load_all = self.enabled_tools == ["all"]
        self.model_string = model_string # llm model string
        self.verbose = verbose
        self.vllm_server_process = None
        self.vllm_config_path = vllm_config_path
        print("\n==> Initializing octotools...")
        print(f"Enabled tools: {self.enabled_tools}")
        print(f"LLM engine name: {self.model_string}")
        self._set_up_tools()
        
        # if vllm, set up the vllm server
        if model_string and model_string.startswith("vllm-"):
            self.setup_vllm_server()

    def get_project_root(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        while current_dir != '/':
            if os.path.exists(os.path.join(current_dir, 'octotools')):
                return os.path.join(current_dir, 'octotools')
            current_dir = os.path.dirname(current_dir)
        raise Exception("Could not find project root")
        
    def _discover_tool_modules(self) -> List[Tuple[str, str]]:
        """
        Discover all tool modules that inherit from BaseTool using Python's import system.
        Returns a list of tuples: (module_path, tool_class_name)
        """
        tool_modules = []
        
        # Get the octotools package location
        try:
            import octotools.tools
            octotools_tools_path = os.path.dirname(octotools.tools.__file__)
        except ImportError:
            print("Warning: Could not import octotools.tools")
            return tool_modules
        
        print(f"Scanning for tools in: {octotools_tools_path}")
        
        # Walk through the tools directory
        for root, dirs, files in os.walk(octotools_tools_path):
            # Skip __pycache__ and other non-tool directories
            dirs[:] = [d for d in dirs if not d.startswith('__') and not d.startswith('.')]
            
            if 'tool.py' in files:
                # Calculate the relative import path
                relative_path = os.path.relpath(root, octotools_tools_path)
                if relative_path == '.':
                    module_path = 'octotools.tools.tool'
                else:
                    module_path = f'octotools.tools.{relative_path.replace(os.sep, ".")}.tool'
                
                tool_modules.append((module_path, relative_path))
        
        return tool_modules

    def _discover_external_tool_modules(self) -> List[Tuple[str, str]]:
        """
        Discover external tool modules from custom directories.
        Returns a list of tuples: (module_path, tool_class_name)
        """
        external_tool_modules = []
        
        # Check for external modules directory
        current_dir = os.getcwd()
        modules_dir = os.path.join(current_dir, 'modules')
        
        if os.path.exists(modules_dir):
            print(f"Scanning for external tools in: {modules_dir}")
            
            # Walk through the modules directory
            for root, dirs, files in os.walk(modules_dir):
                # Skip __pycache__ and other non-tool directories
                dirs[:] = [d for d in dirs if not d.startswith('__') and not d.startswith('.')]
                
                if 'tool.py' in files:
                    # Calculate the relative import path
                    relative_path = os.path.relpath(root, current_dir)
                    module_path = relative_path.replace(os.sep, ".") + ".tool"
                    
                    external_tool_modules.append((module_path, relative_path))
        
        return external_tool_modules

    def load_tools_and_get_metadata(self) -> Dict[str, Any]:
        """Load tools and get metadata using a more robust import-based approach."""
        print("Loading tools and getting metadata...")
        self.toolbox_metadata = {}
        
        # Discover built-in tool modules
        tool_modules = self._discover_tool_modules()
        print(f"Found {len(tool_modules)} built-in tool modules")
        
        # Discover external tool modules
        external_tool_modules = self._discover_external_tool_modules()
        print(f"Found {len(external_tool_modules)} external tool modules")
        
        # Combine all tool modules
        all_tool_modules = tool_modules + external_tool_modules
        
        for module_path, relative_path in all_tool_modules:
            # Skip if we're not loading all tools and this specific tool isn't enabled
            tool_name = os.path.basename(relative_path) if relative_path != '.' else 'tool'
            if not self.load_all and tool_name not in self.available_tools:
                continue
                
            print(f"\n==> Attempting to import: {module_path}")
            
            try:
                module = importlib.import_module(module_path)
                
                # Find classes that inherit from BaseTool
                for name, obj in inspect.getmembers(module):
                    if (inspect.isclass(obj) and 
                        name.endswith('Tool') and 
                        name != 'BaseTool' and
                        hasattr(obj, '__bases__')):
                        
                        # Check if it inherits from BaseTool
                        base_classes = [base.__name__ for base in obj.__mro__]
                        if 'BaseTool' in base_classes:
                            print(f"Found tool class: {name}")
                            
                            try:
                                # Check if the tool requires an LLM engine
                                if hasattr(obj, 'require_llm_engine') and obj.require_llm_engine:
                                    tool_instance = obj(model_string=self.model_string)
                                else:
                                    tool_instance = obj()
                                
                                self.toolbox_metadata[name] = {
                                    'tool_name': getattr(tool_instance, 'tool_name', 'Unknown'),
                                    'tool_description': getattr(tool_instance, 'tool_description', 'No description'),
                                    'tool_version': getattr(tool_instance, 'tool_version', 'Unknown'),
                                    'input_types': getattr(tool_instance, 'input_types', {}),
                                    'output_type': getattr(tool_instance, 'output_type', 'Unknown'),
                                    'demo_commands': getattr(tool_instance, 'demo_commands', []),
                                    'user_metadata': getattr(tool_instance, 'user_metadata', {}),
                                    'require_llm_engine': getattr(obj, 'require_llm_engine', False),
                                }
                                print(f"Metadata for {name}: {self.toolbox_metadata[name]}")
                                
                            except Exception as e:
                                print(f"Error instantiating {name}: {str(e)}")
                                print(traceback.format_exc())
                                
            except Exception as e:
                print(f"Error loading module {module_path}: {str(e)}")
                print(traceback.format_exc())
                        
        print(f"\n==> Total number of tools imported: {len(self.toolbox_metadata)}")
        return self.toolbox_metadata

    def run_demo_commands(self) -> List[str]:
        print("\n==> Running demo commands for each tool...")
        self.available_tools = []

        for tool_name, tool_data in self.toolbox_metadata.items():
            print(f"Checking availability of {tool_name}...")

            try:
                # Find the tool module using the new discovery approach
                tool_modules = self._discover_tool_modules()
                external_tool_modules = self._discover_external_tool_modules()
                all_tool_modules = tool_modules + external_tool_modules
                
                module_found = False
                module = None
                
                for module_path, relative_path in all_tool_modules:
                    try:
                        module = importlib.import_module(module_path)
                        
                        # Check if this module contains the tool class we're looking for
                        for name, obj in inspect.getmembers(module):
                            if (inspect.isclass(obj) and 
                                name == tool_name and
                                hasattr(obj, '__bases__')):
                                
                                # Check if it inherits from BaseTool
                                base_classes = [base.__name__ for base in obj.__mro__]
                                if 'BaseTool' in base_classes:
                                    module_found = True
                                    break
                        
                        if module_found:
                            break
                            
                    except Exception as e:
                        print(f"Error checking module {module_path}: {str(e)}")
                        continue

                if not module_found or module is None:
                    raise ImportError(f"Could not find module for {tool_name}")

                # Get the tool class
                tool_class = None
                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and name == tool_name:
                        tool_class = obj
                        break

                if tool_class is None:
                    raise AttributeError(f"Could not find tool class {tool_name} in module")

                # Instantiate the tool
                if hasattr(tool_class, 'require_llm_engine') and tool_class.require_llm_engine:
                    tool_instance = tool_class(model_string=self.model_string)
                else:
                    tool_instance = tool_class()

                # FIXME This is a temporary workaround to avoid running demo commands
                self.available_tools.append(tool_name)

            except Exception as e:
                print(f"Error checking availability of {tool_name}: {str(e)}")
                print(traceback.format_exc())

        # update the toolmetadata with the available tools
        self.toolbox_metadata = {tool: self.toolbox_metadata[tool] for tool in self.available_tools}
        print("\n✅ Finished running demo commands for each tool.")
        # print(f"Updated total number of available tools: {len(self.toolbox_metadata)}")
        # print(f"Available tools: {self.available_tools}")
        return self.available_tools
    
    def _set_up_tools(self) -> None:
        print("\n==> Setting up tools...")

        # Keep enabled tools
        self.available_tools = [tool.lower().replace('_tool', '') for tool in self.enabled_tools]
        
        # Load tools and get metadata
        self.load_tools_and_get_metadata()
        
        # Run demo commands to determine available tools
        self.run_demo_commands()
        
        # Filter toolbox_metadata to include only available tools
        self.toolbox_metadata = {tool: self.toolbox_metadata[tool] for tool in self.available_tools}
        print("✅ Finished setting up tools.")
        print(f"✅ Total number of final available tools: {len(self.available_tools)}")
        print(f"✅ Final available tools: {self.available_tools}")

    def setup_vllm_server(self) -> None:
        # Check if vllm is installed
        try:
            import vllm
        except ImportError:
            raise ImportError("If you'd like to use VLLM models, please install the vllm package by running `pip install vllm`.")
        
        # Validate config path if provided
        if self.vllm_config_path is not None and not os.path.exists(self.vllm_config_path):
            raise ValueError(f"VLLM config path does not exist: {self.vllm_config_path}")
            
        # Start the VLLM server
        command = ["vllm", "serve", self.model_string.replace("vllm-", ""), "--port", "8888"]
        if self.vllm_config_path is not None:
            command = ["vllm", "serve", "--config", self.vllm_config_path, "--port", "8888"]

        import subprocess
        vllm_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        print("Starting VLLM server...")
        while True:
            output = vllm_process.stdout.readline()
            error = vllm_process.stderr.readline()
            time.sleep(5)
            if output.strip() != "":
                print("VLLM server standard output:", output.strip())
            if error.strip() != "":
                print("VLLM server standard error:", error.strip())

            if "Application startup complete." in output or "Application startup complete." in error:
                print("VLLM server started successfully.")
                break

            if vllm_process.poll() is not None:
                print("VLLM server process terminated unexpectedly. Please check the output above for more information.")
                break

        self.vllm_server_process = vllm_process

if __name__ == "__main__":
    enabled_tools = ["Generalist_Solution_Generator_Tool"]
    initializer = Initializer(enabled_tools=enabled_tools)

    print("\nAvailable tools:")
    print(initializer.available_tools)

    print("\nToolbox metadata for available tools:")
    print(initializer.toolbox_metadata)
    