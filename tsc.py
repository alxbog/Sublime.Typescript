import sublime, sublime_plugin, os, subprocess, re, datetime, threading

class TypescriptCommand(sublime_plugin.EventListener):
    def on_post_save(self, view):
        thread = Builder(view)
        thread.start()

class Builder(threading.Thread):
    def __init__(self, view):
        self.view = view
        threading.Thread.__init__(self)

    def run(self):
        view = self.view
        settings = sublime.load_settings("tsc.sublime-settings")
        folders = view.window().folders()

        if len(folders) == 0:
            return

        root = folders[0]
        src = root + settings.get("src")

        print("-----------------------------------------------")
        
        dependency_resolver = TypescriptDependencyResolver(src)
        src_files = dependency_resolver.collect_files()
        if len(src_files) == 0:
            return

        now = datetime.datetime.now()
        print("Resolving dependencies...")
        dependencies = []
        for f in src_files:
            resolved_dependencies = dependency_resolver.resolve(f)
            for d in resolved_dependencies:
                if not d in dependencies:
                    dependencies.append(d)

        now = datetime.datetime.now()
        print("Compiling sources...")
        build_result = self.build(settings.get("node"), settings.get("tsc"), dependencies, root + settings.get("out"))

        self.report_result(view, build_result)


    def report_result(self, view, build_result):
        for v in view.window().views():
            v.erase_regions("tsc_errors")

        if len(build_result[1]) > 0:
            print("Build failed.")
            errors = self.parse_errors(build_result[1].decode("utf-8"))
            self.report_error_result(view, errors)
            view.set_status("Typescript", "Build failed")
        else:
            print("Build succeeded.")
            view.set_status("Typescript", "Build succeeded")


    def report_error_result(self, view, errors):
        files = []
        panel_items = []
        for error in errors:
            if not error.file_path in files:
                files.append(error.file_path)

            item = [error.message, u'{0}: {1}'.format(error.file_path, error.line)]
            panel_items.append(item)

        for f in files:
            regions = []
            error_view = self.find_view(view, f)
            if error_view != None:
                for error in errors:
                    if error.file_path == f:
                        begin = error_view.text_point(error.line - 1, 0) + error.line_pos - 1
                        regions.append(sublime.Region(begin, begin))

                error_view.add_regions(
                    "tsc_errors", 
                    regions,
                    "tsc.errors",
                    "cross",
                    sublime.DRAW_EMPTY_AS_OVERWRITE)

        def on_done(selected_item):
            if selected_item == -1:
                return

            error = errors[selected_item]
            error_view = self.find_view(view, error.file_path)

            if error_view == None:
                error_view = view.window().open_file(error.file_path)

            if error_view != view:
                view.window().focus_view(error_view)

            begin = error_view.text_point(error.line - 1, 0) + error.line_pos - 1

            selected = error_view.sel()
            selected.clear()
            selected.add(sublime.Region(begin, begin))
            error_view.run_command('move', {'by': 'characters', 'forward': True})
            error_view.run_command('move', {'by': 'characters', 'forward': False})
            error_view.show_at_center(begin)

        view.window().show_quick_panel(panel_items, on_done)


    def find_view(self, view, file_path):
        file_path = file_path.strip()

        if view.file_name() == file_path:
            return view;

        for v in view.window().views():
            if v.file_name() == file_path:
                return v

        return None

    def parse_errors(self, error_message):
        appended_lines = []
        result = []
        lines = error_message.split('\n')
        for line in lines:
            if not line in appended_lines:
                m = re.search('([^\(]*)\((\d+),(\d+)\):\s+((.*[\s\r\n]*.*)+)\s*$', line)
                if m != None:
                    error = TypescriptError(m.group(1), m.group(4), int(m.group(2)), int(m.group(3)))
                    result.append(error)

                appended_lines.append(line)
        return result


    def build(self, node, tsc, files, output):
        cmd = node + ' ' + tsc + ' --out ' + output
        for f in files:
            cmd = cmd + ' ' + f

        return subprocess.Popen([cmd], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()


# --------------------------------------------------------------------------------
# Class which provides source files sequence in dependency order
# --------------------------------------------------------------------------------
class TypescriptDependencyResolver:
    def __init__(self, src_path):
        self.src_path = src_path
        self.src_files = []
        self.resolved_dependencies = []
        self.initialized = False
        self.type_matcher = re.compile('((interface)|(class))\s+([a-zA-Z0-9]+)')

    def resolve(self, file_path):
        if not self.initialized:
            self.collect_files()
            self.collect_declarations()
            self.collect_dependencies()
            self.initialized = True

        self.resolved_dependencies = []
        self.resolve_file_dependency(file_path, [])

        return self.resolved_dependencies

    def collect_files(self):
        if (len(self.src_files) > 0):
            return self.src_files

        self.collect_file_from_directory(self.src_path)
        return self.src_files

    def collect_file_from_directory(self, path):
        for path, dirs, files in os.walk(path):
            for f in files:
                if f.endswith(".ts"):
                    self.src_files.append(path + "/" + f)

            for d in dirs:
                self.collect_file_from_directory(d)

    def collect_declarations(self):
        self.declarations = []
        for f in self.src_files:
            self.collect_declarations_from_file(f)

    def collect_declarations_from_file(self, file_path):
        f = open(file_path, "r", encoding="utf-8")
        for line in f:
            m = self.type_matcher.search(line)
            if m != None:
                declaration = TypescriptTypeDeclaration(m.group(4), file_path)
                self.declarations.append(declaration)

    def collect_dependencies(self):
        self.dependencies = []
        for f in self.src_files:
            dependency = TypescriptFileDependency(f, self.collect_file_dependencies(f))
            self.dependencies.append(dependency)

    def collect_file_dependencies(self, file_path):
        f = open(file_path, "r", encoding="utf-8")
        source = f.read()
        dependencies = []
        for declaration in self.declarations:
            if declaration.file_path != file_path:
                if not declaration.file_path in dependencies:
                    if declaration.type_name in source:
                        dependencies.append(declaration.file_path)

        return dependencies

    def resolve_file_dependency(self, file_path, stack):
        if file_path in self.resolved_dependencies:
            return

        if file_path in stack:
            return

        stack.append(file_path)
        dependency = self.find_file_dependency(file_path)
        dependencies = dependency.dependencies

        if len(dependencies) == 0:
            self.resolved_dependencies.append(file_path)
            return

        for d in dependencies:
            self.resolve_file_dependency(d, stack)

        self.resolved_dependencies.append(file_path)

    def find_file_dependency(self, file_path):
        for d in self.dependencies:
            if (d.file_path == file_path):
                return d

        return None


# --------------------------------------------------------------------------------
# Represents a very simple typescript type declaration
# --------------------------------------------------------------------------------
class TypescriptTypeDeclaration:
    def __init__(self, type_name, file_path):
        self.type_name = type_name
        self.file_path = file_path


# --------------------------------------------------------------------------------
# Represents a typescript file dependency
# --------------------------------------------------------------------------------
class TypescriptFileDependency:
    def __init__(self, file_path, dependencies):
        self.file_path = file_path
        self.dependencies = dependencies


# --------------------------------------------------------------------------------
# Represents a typescript error
# --------------------------------------------------------------------------------
class TypescriptError:
    def __init__(self, file_path, message, line, line_pos):
        self.file_path = file_path
        self.message = message
        self.line = line
        self.line_pos = line_pos

