import tkinter as tk
from tkinter import ttk
import builtins
from typing import List, Optional, Union, Iterable, Tuple
from thonny import ui_utils, tktextext, get_workbench, get_runner,\
    rst_utils
from collections import namedtuple
import re
import os.path
from thonny.common import ToplevelResponse
import ast
from thonny.misc_utils import levenshtein_damerau_distance
import token
import tokenize

import subprocess
from thonny.running import get_frontend_python
import sys
import textwrap
from thonny.ui_utils import scrollbar_style


Suggestion = namedtuple("Suggestion", ["title", "body", "relevance"])

_program_analyzer_classes = []

class AssistantView(tktextext.TextFrame):
    def __init__(self, master):
        tktextext.TextFrame.__init__(self, master, 
                                     text_class=AssistantRstText, 
                                     vertical_scrollbar_style=scrollbar_style("Vertical"), 
                                     horizontal_scrollbar_style=scrollbar_style("Horizontal"),
                                     horizontal_scrollbar_class=ui_utils.AutoScrollbar,
                                     read_only=True,
                                     wrap="word",
                                     font="TkDefaultFont",
                                     #cursor="arrow",
                                     padx=10,
                                     pady=0,
                                     insertwidth=0)
        
        self._error_helper_classes = {
            "NameError" : {NameErrorHelper},
            "SyntaxError" : {SyntaxErrorHelper},
        }
        
        self._analyzer_instances = [
            cls(self._accept_warnings) for cls in _program_analyzer_classes
        ]
        
        self._accepted_warning_sets = []
        
        self._suggestions = set()
        
        self.text.tag_configure("section_title",
                                spacing3=5,
                                font="BoldTkDefaultFont",
                                #foreground=get_syntax_options_for_tag("stderr")["foreground"]
                                )
        self.text.tag_configure("intro", 
                                #font="ItalicTkDefaultFont", 
                                spacing3=10)
        self.text.tag_configure("relevant_suggestion_title", font="BoldTkDefaultFont")
        self.text.tag_configure("suggestion_title", lmargin2=16, spacing1=5, spacing3=5)
        self.text.tag_configure("suggestion_body", lmargin1=16, lmargin2=16)
        self.text.tag_configure("body", font="ItalicTkDefaultFont")
        
        get_workbench().bind("ToplevelResponse", self._handle_toplevel_response, True)
    
    def add_error_helper(self, error_type_name, helper_class):
        if error_type_name not in self._error_helper_classes:
            self._error_helper_classes[error_type_name] = []
        self._error_helper_classes[error_type_name].append(helper_class)
    
    def _handle_toplevel_response(self, msg: ToplevelResponse) -> None:
        self._clear()
        
        if "user_exception" in msg:
            self._explain_exception(msg["user_exception"])
        
        if "filename" in msg:
            self._start_program_analyses(msg["filename"])
    
    def _explain_exception(self, error_info):
        "►▸˃✶ ▼▸▾"
        
        rst = (".. default-role:: code\n\n"
               + rst_utils.create_title(error_info["type_name"]
                                        + ": " 
                                        + rst_utils.escape(error_info["message"]))
               + "\n")
        
        if error_info.get("lineno") is not None:
            rst += (
                "`%s, line %d <%s>`__\n\n" % (
                    os.path.basename(error_info["filename"]),
                    error_info["lineno"],
                    self._format_file_url(error_info)
                )
            )
        
        if error_info["type_name"] not in self._error_helper_classes:
            rst += "No helpers for this error type\n"
        else:
            helpers =[helper_class(error_info)
                  for helper_class in self._error_helper_classes[error_info["type_name"]]]
            
            # TODO: how to select the intro text if there are several helpers?
            rst += ("*" 
                    + helpers[0].get_intro().replace("\n\n", "*\n\n*")
                    + "*\n\n")
            
            suggestions = [suggestion 
                           for helper in helpers
                           for suggestion in helper.get_suggestions()]
            
            for i, suggestion in enumerate(
                sorted(suggestions, key=lambda s: s.relevance, reverse=True)
                ):
                if suggestion.relevance > 0:
                    rst += self._format_suggestion(suggestion, i==0)
        
        self.text.append_rst(rst)
        self._append_text("\n")
        
    
    def _format_suggestion(self, suggestion, initially_open):
        return (
            # assuming that title is already in rst format
            ".. topic:: " + suggestion.title + "\n"
          + "    :class: toggle%s\n" % (" open" if initially_open else "")
          + "    \n"
          + textwrap.indent(suggestion.body, "    ") + "\n\n"
        )
        
    
    def _append_text(self, chars, tags=()):
        self.text.direct_insert("end", chars, tags=tags)
    
    
    def _clear(self):
        self._suggestions.clear()
        self._accepted_warning_sets.clear()
        for wp in self._analyzer_instances:
            wp.cancel_analysis()
        self.text.clear()
    
    def _start_program_analyses(self, filename):
        for wp in self._analyzer_instances:
            wp.start_analysis(filename)
        
        self._append_text("\nAnalyzing your code ...", ("em",))
    
    def _accept_warnings(self, title, warnings):
        self._accepted_warning_sets.append(warnings)
        if len(self._accepted_warning_sets) == len(self._analyzer_instances):
            # all providers have reported
            all_warnings = [w for ws in self._accepted_warning_sets for w in ws]
            self._present_warnings(all_warnings)
    
    def _present_warnings(self, warnings):
        self.text.direct_delete("end-2l linestart", "end-1c lineend")
        
        if not warnings:
            return
        
        #self._append_text("\n")
        # TODO: show filename when more than one file was analyzed
        # Put main file first
        # TODO: group by file and confidence
        rst = (
            ".. default-role:: code\n"
            + "\n"
            + rst_utils.create_title("Warnings")
            + "*May be ignored if you are happy with your program.*\n\n"
        )
        
        by_file = {}
        for warning in warnings:
            if warning["filename"] not in by_file:
                by_file[warning["filename"]] = []
            by_file[warning["filename"]].append(warning)
        
        for filename in by_file:
            rst += "`%s <%s>`__\n\n" % (os.path.basename(filename),
                                            self._format_file_url(dict(filename=filename)))
            for warning in sorted(by_file[filename], key=lambda x: x["lineno"]):
                rst += self._format_warning(warning) + "\n"
        
        self.text.append_rst(rst)
    
    def _format_warning(self, warning):
        title = rst_utils.escape(warning["msg"].splitlines()[0])
        if warning.get("lineno") is not None:
            url = self._format_file_url(warning)
            title = "`Line %d <%s>`__: %s" % (warning["lineno"], url, title)
        
        if warning.get("explanation_rst"):
            explanation_rst = warning["explanation_rst"]
        elif warning.get("explanation"):
            explanation_rst = rst_utils.escape(warning["explanation"])
        else:
            explanation_rst = ""
        
        if warning.get("more_info_url"):
            explanation_rst += "\n\n`More info online <%s>`__" % warning["more_info_url"]
        
        explanation_rst = explanation_rst.strip()
        if not explanation_rst:
            explanation_rst = "Perform a web search with 'Python' and the above message for more info."
        
        return (
            ".. topic:: %s\n" % title
            + "    :class: toggle\n"
            + "    \n"
            + textwrap.indent(explanation_rst, "    ") + "\n\n"
        )
    
    def _format_file_url(self, atts):
        assert atts["filename"]
        s = "thonny://" + rst_utils.escape(atts["filename"])
        if atts.get("lineno") is not None:
            s += "#" + str(atts["lineno"])
            if atts.get("col_offset") is not None:
                s += ":" + str(atts["col_offset"])
        
        return s
    
class AssistantRstText(rst_utils.RstText):
    def configure_tags(self):
        rst_utils.RstText.configure_tags(self)
        
        main_font = tk.font.nametofont("TkDefaultFont")
        
        italic_font = main_font.copy()
        italic_font.configure(slant="italic", size=main_font.cget("size"))
        
        h1_font = main_font.copy()
        h1_font.configure(weight="bold", 
                          size=main_font.cget("size"))
        
        self.tag_configure("h1", font=h1_font, spacing3=0, spacing1=10)
        self.tag_configure("topic_title", font="TkDefaultFont")
        
        self.tag_configure("topic_body", font=italic_font)

        self.tag_raise("sel")

class Helper:
    def get_intro(self):
        raise NotImplementedError()
    
    def get_suggestions(self) -> Iterable[Suggestion]:
        raise NotImplementedError()

class DebugHelper(Helper):
    pass

class SubprocessProgramAnalyzer:
    def __init__(self, on_completion):
        self._proc = None
        self.completion_handler = on_completion
        
    def start_analysis(self, filename):
        pass
    
    def cancel_analysis(self):
        if self._proc is not None:
            self._proc.kill()

        
class ErrorHelper(Helper):
    def __init__(self, error_info):
        
        # TODO: don't repeat all this for all error helpers
        self.intro_is_enough = False
        self.error_info = error_info
        
        self.last_frame = error_info["stack"][-1]
        self.last_frame_ast = None
        if self.last_frame.source:
            try:
                self.last_frame_ast = ast.parse(self.last_frame.source,
                                                self.last_frame.filename)
            except SyntaxError:
                pass
            
        
        self.last_frame_module_source = None 
        self.last_frame_module_ast = None
        if self.last_frame.code_name == "<module>":
            self.last_frame_module_source = self.last_frame.source
            self.last_frame_module_ast = self.last_frame_ast
        elif self.last_frame.filename is not None:
            with tokenize.open(self.last_frame.filename) as fp:
                self.last_frame_module_source = fp.read() 
            try:
                self.last_frame_module_ast = ast.parse(self.last_frame_module_source)
            except SyntaxError:
                pass
        
        

class LibraryErrorHelper(ErrorHelper):
    """Explains exceptions, which doesn't happen in user code"""
    
    def get_intro(self):
        return "This error happened in library code. This may mean a bug in "
    
    def get_suggestions(self):
        return []

class SyntaxErrorHelper(ErrorHelper):
    def __init__(self, error_info):
        ErrorHelper.__init__(self, error_info)
        
        # NB! Stack info is not relevant with SyntaxErrors,
        # use special fields instead

        self.tokens = []
        self.token_error = None
        
        
        if self.error_info["filename"]:
            with open(self.error_info["filename"], mode="rb") as fp:
                try:
                    for t in tokenize.tokenize(fp.readline):
                        self.tokens.append(t)
                except tokenize.TokenError as e:
                    self.token_error = e
            
            assert self.tokens[-1].type == token.ENDMARKER
        else:
            self.tokens = None
            
            
    def get_intro(self):
        if self.error_info["message"] == "EOL while scanning string literal":
            self.intro_is_enough = True
            return ("You haven't properly closed the string on line %s." % self.error_info["lineno"]
                    + "\n(If you want a multi-line string, then surround it with"
                    + " `'''` or `\"\"\"` at both ends.)")
            
        elif self.error_info["message"] == "EOF while scanning triple-quoted string literal":
            # lineno is not useful, as it is at the end of the file and user probably
            # didn't want the string to end there
            return "You haven't properly closed a triple-quoted string"
            self.intro_is_enough = True
        else:
            msg = "Python doesn't know how to read your program."
            
            if True: # TODO: check the presence of ^
                msg += (" Small `^` in the original error message shows where it gave up,"
                        + " but the actual mistake can be before this.") 
            
            return msg
    
    def get_more_info(self):
        return "Even single wrong, misplaced or missing character can cause syntax errors."
    
    def get_suggestions(self) -> Iterable[Suggestion]:
        return [self._sug_missing_or_misplaced_colon()]
    
    def _sug_missing_or_misplaced_colon(self):
        i = 0
        title = "Did you forget the colon?"
        relevance = 0
        body = ""
        while i < len(self.tokens) and self.tokens[i].type != token.ENDMARKER:
            t = self.tokens[i]
            if t.string in ["if", "elif", "else", "while", "for", "with",
                            "try", "except", "finally", 
                            "class", "def"]:
                keyword_pos = i
                while (self.tokens[i].type not in [token.NEWLINE, token.ENDMARKER, 
                                     token.COLON, # colon may be OP 
                                     token.RBRACE]
                        and self.tokens[i].string != ":"):
                    
                    old_i = i
                    if self.tokens[i].string in "([{":
                        i = self._skip_braced_part(i)
                        assert i > old_i
                    else:
                        i += 1
                
                if self.tokens[i].string != ":":
                    relevance = 9
                    body = "`%s` header must end with a colon." % t.string
                    break
            
                # Colon was present, but maybe it should have been right
                # after the keyword.
                if (t.string in ["else", "try", "finally"]
                    and self.tokens[keyword_pos+1].string != ":"):
                    title = "Incorrect use of `%s`" % t.string
                    body = "Nothing is allowed between `%s` and colon." % t.string
                    relevance = 9
                    if (self.tokens[keyword_pos+1].type not in (token.NEWLINE, tokenize.COMMENT)
                        and t.string == "else"):
                        body = "If you want to specify a conditon, then use `elif` or nested `if`."
                    break
                
            i += 1
                
        return Suggestion(title, body, relevance)
    
    def _sug_wrong_increment_op(self):
        pass
    
    def _sug_wrong_decrement_op(self):
        pass
    
    def _sug_wrong_comparison_op(self):
        pass
    
    def _sug_switched_assignment_sides(self):
        pass
    
    def _skip_braced_part(self, token_index):
        assert self.tokens[token_index].string in "([{"
        level = 1
        while token_index < len(self.tokens):
            token_index += 1
            
            if self.tokens[token_index].string in "([{":
                level += 1
            elif self.tokens[token_index].string in ")]}":
                level -= 1
            
            if level <= 0:
                token_index += 1
                return token_index
        
        assert token_index == len(self.tokens)
        return token_index-1
    
    def _find_first_braces_problem(self):
        #closers = {'(':')', '{':'}', '[':']'}
        openers = {')':'(', '}':'{', ']':'['}
        
        brace_stack = []
        for t in self.tokens:
            if t.string in "([{":
                brace_stack.append(token)
            elif t.string in ")]}":
                if not brace_stack:
                    return (t, "`%s` without preceding matching `%s`" % (t.string, openers[t.string]))
                elif brace_stack[-1].string != openers[t.string]:     
                    return (t, "`%s` when last unmatched opener was `%s`" % (t.string, brace_stack[-1].string))
                else:
                    brace_stack.pop()
        
        if brace_stack:
            return (brace_stack[-1], "`%s` was not closed by the end of the program" % brace_stack[-1].string)
        
        return None
        

class NameErrorHelper(ErrorHelper):
    def __init__(self, error_info):
        
        super().__init__(error_info)
        
        names = re.findall(r"\'.*\'", error_info["message"])
        assert len(names) == 1
        self.name = names[0].strip("'")
    
    def get_intro(self):
        # TODO: add link to source
        return "Python doesn't know what `%s` stands for." % self.name
    
    def get_suggestions(self):
        
        return [
            self._sug_bad_spelling(),
            self._sug_missing_quotes(),
            self._sug_missing_import(),
            self._sug_local_from_global(),
            self._sug_not_defined_yet(),
        ]
    
    def _sug_missing_quotes(self):
        # TODO: only when in suitable context for string
        if (self._is_attribute_value() 
            or self._is_call_function()
            or self._is_subscript_value()):
            relevance = 0
        else:
            relevance = 5
            
        return Suggestion(
            "Did you actually mean string (text)?",
            'If you didn\'t mean a variable but literal text "%s", then surround it with quotes.' % self.name,
            relevance
        )
    
    def _sug_bad_spelling(self):
        
        # Yes, it would be more proper to consult builtins from the backend,
        # but it's easier this way...
        all_names = {name for name in dir(builtins) if not name.startswith("_")}
        all_names |= {"pass", "break", "continue", "return", "yield"}
        
        if self.last_frame.globals is not None:
            all_names |= set(self.last_frame.globals.keys())
        if self.last_frame.locals is not None:
            all_names |= set(self.last_frame.locals.keys())
        
        similar_names = {self.name}
        if all_names:
            relevance = 0
            for name in all_names:
                sim = _name_similarity(name, self.name)
                if sim > 4:
                    similar_names.add(name)
                relevance = max(sim, relevance)
        else:
            relevance = 3
        
        if len(similar_names) > 1:
            body = "Are following names meant to be different?\n\n"
            for name in sorted(similar_names, key=lambda x: x.lower()):
                # TODO: add links to source
                body += "* `%s`\n\n" % name
        else:
            body = (
                "Compare the name with corresponding definition / assignment / documentation."
                + " Don't forget that case of the letters matters."
            ) 
        
        return Suggestion(
            "Did you misspell it (somewhere)?",
            body,
            relevance
        )
    
    def _sug_missing_import(self):
        likely_importable_functions = {
            "math" : {"ceil", "floor", "sqrt", "sin", "cos", "degrees"},  
            "random" : {"randint"},
            "turtle" : {"left", "right", "forward", "fd", 
                        "goto", "setpos", "Turtle",
                        "penup", "up", "pendown", "down",
                        "color", "pencolor", "fillcolor",
                        "begin_fill", "end_fill", "pensize", "width"},
            "re" : {"search", "match", "findall"},
            "datetime" : {"date", "time", "datetime", "today"},
            "statistics" : {"mean", "median", "median_low", "median_high", "mode", 
                            "pstdev", "pvariance", "stdev", "variance"},
            "os" : {"listdir"},
            "time" : {"time", "sleep"},
        }
        
        body = None
         
        if self._is_call_function():
            relevance = 5
            for mod in likely_importable_functions:
                if self.name in likely_importable_functions[mod]:
                    relevance += 3
                    body = ("If you meant `%s` from module `%s`, then add\n\n`from %s import %s`\n\nto the beginning of your script."
                                % (self.name, mod, mod, self.name))
                    break
                
        elif self._is_attribute_value():
            relevance = 5
            body = ("If you meant module `%s`, then add `import %s` to the beginning of your script"
                        % (self.name, self.name))
            
            if self.name in likely_importable_functions:
                relevance += 3
                
                
        elif self._is_subscript_value() and self.name != "argv":
            relevance = 0
        elif self.name == "pi":
            body = "If you meant the constant π, then add `from math import pi` to the beginning of your script."
            relevance = 8
        elif self.name == "argv":
            body = "If you meant the list with program arguments, then add `from sys import argv` to the beginning of your script."
            relevance = 8
        else:
            relevance = 3
            
        
        if body is None:
            body = "Some functions/variables need to be imported before they can be used."
            
        return Suggestion("Did you forget to import it?",
                           body,
                           relevance)
    
    def _sug_local_from_global(self):
        relevance = 0
        body = None
        
        if self.last_frame.code_name == "<module>":
            function_names = set()
            for node in ast.walk(self.last_frame_module_ast):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if self.name in map(lambda x: x.arg, node.args.args):
                        function_names.add(node.name)
                    # TODO: varargs, kw, ...
                    declared_global = False
                    for localnode in ast.walk(node):
                        #print(node.name, localnode)
                        if (isinstance(localnode, ast.Name)
                            and localnode.id == self.name
                            and isinstance(localnode.ctx, ast.Store)
                            ):
                            function_names.add(node.name)
                        elif (isinstance(localnode, ast.Global)
                              and self.name in localnode.names):
                            declared_global = True
                    
                    if node.name in function_names and declared_global:
                        function_names.remove(node.name)
            
            if function_names:
                relevance = 9
                body = (
                    ("Name `%s` defined in `%s` is not accessible in the global/module level."
                     % (self.name, " and ".join(function_names)))
                    + "\n\nIf you need that data at the global level, then consider changing the function so that it `return`-s the value.")
            
        return Suggestion("Are you trying to acces a local variable outside of the function?",
                          body,
                          relevance)
    
    def _sug_not_defined_yet(self):
        return Suggestion("Has Python executed the definition?",
                          ("Don't forget that name becomes defined when corresponding definition ('=', 'def' or 'import') gets executed."
                          + " If the definition comes later in code or is inside an if-statement, Python may not have executed it (yet)."
                          + "\n\n"
                          + "Make sure Python arrives to the definition before it arrives to this line. When in doubt, use the debugger."),
                          1)
    
    def _is_call_function(self):
        return self.name + "(" in (self.error_info["line"]
                                   .replace(" ", "")
                                   .replace("\n", "")
                                   .replace("\r", ""))
                                   
    def _is_subscript_value(self):
        return self.name + "[" in (self.error_info["line"]
                                   .replace(" ", "")
                                   .replace("\n", "")
                                   .replace("\r", ""))
                                   
    def _is_attribute_value(self):
        return self.name + "." in (self.error_info["line"]
                                   .replace(" ", "")
                                   .replace("\n", "")
                                   .replace("\r", ""))
        
    
def _name_similarity(a, b):
    # TODO: tweak the result values
    a = a.replace("_", "")
    b = b.replace("_", "")
    
    minlen = min(len(a), len(b))
    
    if (a.replace("0", "O").replace("1", "l")
          == b.replace("0", "O").replace("1", "l")):
        if minlen >= 4: 
            return 7
        else:
            return 6
    
    a = a.lower()
    b = b.lower()
    
    if a == b:
        if minlen >= 4: 
            return 7
        else:
            return 6
    
    
    if minlen <= 2:
        return 0
    
    # if names differ at final isolated digits, 
    # then they are probably different vars, even if their
    # distance is small (eg. location_1 and location_2)
    if (a[-1].isdigit() and not a[-2].isdigit() 
        and b[-1].isdigit() and not b[-2].isdigit()):
        return 0
    
    # same thing with _ + single char suffixes
    # (eg. location_a and location_b)
    if a[-2] == "_" and b[-2] == "_":
        return 0
    
    distance = levenshtein_damerau_distance(a, b, 5)
    
    if minlen <= 5:
        return max(8 - distance*2, 0)
    elif minlen <= 10:
        return max(9 - distance*2, 0)
    else:
        return max(10 - distance*2, 0)
        

def _get_imported_user_files(main_file):
    assert os.path.isabs(main_file)
    
    with tokenize.open(main_file) as fp:
        source = fp.read()
    
    try:
        root = ast.parse(source, main_file)
    except SyntaxError:
        return set()
    
    main_dir = os.path.dirname(main_file)
    module_names = set()
    # TODO: at the moment only considers non-package modules
    for node in ast.walk(root):
        if isinstance(node, ast.Import):
            for item in node.names:
                module_names.add(item.name)
        elif isinstance(node, ast.ImportFrom):
            module_names.add(node.name)
    
    imported_files = set()
    
    for file in {name + ext for ext in [".py", ".pyw"] for name in module_names}:
        possible_path = os.path.join(main_dir, file)
        if os.path.exists(possible_path):
            imported_files.add(possible_path)
    
    # TODO: add recursion
    
    return imported_files
    
def add_program_analyzer(cls):
    _program_analyzer_classes.append(cls)
