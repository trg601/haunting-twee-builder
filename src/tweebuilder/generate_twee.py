import asyncio
import logging
import os
import re
import time
from enum import Enum

import aiofiles
import aiofiles.os
from merman import MermanEngine
from pydantic import BaseModel

from tweebuilder.gcp_service import GCPService
from tweebuilder.twine_config import global_twine_config

logger = logging.getLogger(__name__)


class SegmentType(int, Enum):
    LINK = 1
    MACRO = 2


class Link(BaseModel):
    node_id: str
    display_name: str
    setter: str | None = None
    raw_text: str
    link_type: SegmentType = SegmentType.LINK


class Node(BaseModel):
    node_id: str
    content: str
    links: list[Link] = []
    tab_path: str
    current_loop: int | None = None
    current_track: str | None = None

    def relink(self, link: Link, new_id: str):
        old_inner_text = link.raw_text
        if link.link_type == SegmentType.MACRO:
            new_inner_text = f'<link "{link.display_name}" "{new_id}">'
        else:
            setter_text = f"[{link.setter}]" if link.setter else ""
            new_inner_text = f"[[{link.display_name}|{new_id}]{setter_text}]"
        self.content = self.content.replace(
            old_inner_text,
            new_inner_text,
        )
        link.node_id = new_id
        link.raw_text = new_inner_text


class Document(BaseModel):
    nodes: list[Node] = []
    tab_name_lookup: dict[str, str] = {}
    start_node: Node


TAB_LENGTH_PTS = 36
ESCAPE_CHAR_MAP = {"\v": "\n", "�": "'"}


class ParsedParagraph(BaseModel):
    indent: int
    content: str
    links: list[Link] = []


def parse_raw_paragraph(p: dict) -> ParsedParagraph:
    p_style = p.get("paragraphStyle", {})
    indent = (p_style.get("indentStart") or p_style.get("indentFirstLine", {})).get(
        "magnitude", 0
    ) / TAB_LENGTH_PTS
    content = ""
    links: list[Link] = []

    comment_stack: list[str] = []
    script_escape_stack: list[str] = []
    argument_text = ""
    arguments: list[str] = []
    segment_type: SegmentType | None = None
    segment_text = ""
    for element in p.get("elements", []):
        text: str = element.get("textRun", {}).get("content", "")
        if not text:
            continue
        check_tabs = True
        previous_char = ""
        for char in text:
            if check_tabs:
                if char == "\t":
                    indent += 1
                    continue
                check_tabs = False
            if char == "*":
                # Comment toggle
                if not comment_stack:
                    comment_stack.append("*")
                elif comment_stack[-1] == "*" and char == "*":
                    comment_stack.pop()
            elif char == "(" and not previous_char.strip(" \t\n"):
                # Comment Begin
                comment_stack.append("(")
            elif char == ")" and comment_stack and comment_stack[-1] == "(":
                # Comment End
                comment_stack.pop()
            elif not comment_stack:
                if replacement := ESCAPE_CHAR_MAP.get(char):
                    char = replacement
                if char == "\n" and previous_char in ("*", ")"):
                    # newline after comment, skip it
                    continue
                elif char == "[" and previous_char == "[":
                    # Start link
                    if not segment_type:
                        segment_type = SegmentType.LINK
                        segment_text = ""
                        script_escape_stack = []
                    else:
                        script_escape_stack.append("[")
                elif char == "]" and previous_char == "]":
                    if segment_type == SegmentType.LINK:
                        # End link
                        segment_type = None
                        link_text = segment_text[1:-1]
                        setter_parts = link_text.split("][")
                        setter = setter_parts[1] if len(setter_parts) > 1 else None
                        link_parts = setter_parts[0].split("|")
                        link = Link(
                            node_id=normalize_id(link_parts[-1]),
                            display_name=link_parts[0],
                            setter=setter,
                            raw_text=f"[{segment_text}]",
                        )
                        links.append(link)
                    elif script_escape_stack and script_escape_stack[-1] == "[":
                        script_escape_stack.pop()
                elif char == "<" and previous_char == "<":
                    if not segment_type:
                        # Start script
                        argument_text = ""
                        arguments = []
                        segment_type = SegmentType.MACRO
                        segment_text = ""
                    else:
                        script_escape_stack.append("<")
                elif char == ">" and previous_char == ">":
                    if segment_type == SegmentType.MACRO:
                        # End script
                        segment_type = None
                        arguments.append(argument_text[:-1])
                        arg_len = len(arguments)
                        script_type = arguments[0][1:] if arg_len >= 1 else ""
                        if script_type == "link" and arg_len >= 2:
                            node_id = arguments[1]
                            display_name = arguments[2] if arg_len >= 3 else node_id
                            if re.search(r"\[\[([\w, ?!'|\-\+]+)\]\]", string=node_id):
                                # Link has a markup format embedded, parse out node id and display name
                                parts = node_id[2:-2].split("|")
                                node_id = parts[-1]
                                display_name = parts[0]
                            link = Link(
                                node_id=normalize_id(node_id),
                                display_name=display_name,
                                raw_text=segment_text,
                                link_type=SegmentType.MACRO,
                            )
                            links.append(link)
                    elif script_escape_stack and script_escape_stack[-1] == "<":
                        script_escape_stack.pop()
                if segment_type == SegmentType.MACRO:
                    if char == '"':
                        if not script_escape_stack:
                            script_escape_stack.append('"')
                        elif script_escape_stack[-1] == '"':
                            script_escape_stack.pop()
                    elif char == " " and not script_escape_stack:
                        # End of argument
                        arguments.append(argument_text)
                        argument_text = ""
                    else:
                        argument_text += char
                if segment_type:
                    segment_text += char
                content += char
            previous_char = char
    return ParsedParagraph(indent=indent, content=content, links=links)


def normalize_id(name: str) -> str:
    """Convert a node ID into a standardized format."""
    return "-".join(p.strip() for p in name.strip(" \n\t\r").lower().split("-"))


SPECIAL_LINKS = {
    "h-appy": "loop {current_loop}-happy-{current_track}",
    "h-amp": "loop {current_loop}-hampered-{current_track}",
    "h-aun": "loop {current_loop}-haunted-{current_track}",
    "pia": "loop {current_loop}-surrounding loop-{current_track}",
    "do": "loop {next_loop}-surrounding loop-{current_track}",  # TODO: Create proper DO/DO NOT sections
    "do not": "loop {next_loop}-surrounding loop-{current_track}",
}


def add_node(
    tab_path: str,
    nodes: list[Node],
    seen_id_counter: dict[str, int],
    name: str,
    content: str,
    links: list[Link],
    current_loop: int | None,
    current_track: str | None,
) -> None:
    """Add a node to the list of nodes, ensuring unique names."""
    content = content.rstrip("\n").lstrip("\n")
    # Add debug metadata
    content += f"<<set $tab_path = '{tab_path}'>><<set $current_loop = {current_loop}>><<set $current_track = '{current_track}'>>\n"

    node = Node(
        node_id=name,
        content=content,
        links=links,
        tab_path=tab_path,
        current_loop=current_loop,
        current_track=current_track,
    )

    for link in links:
        if link.node_id in SPECIAL_LINKS and current_loop and current_track:
            # Special cases, link based on defined format
            node.relink(
                link,
                SPECIAL_LINKS[link.node_id].format(
                    current_loop=current_loop,
                    current_track=current_track,
                    next_loop=current_loop + 1,
                ),
            )
        elif (current_count := seen_id_counter.get(link.node_id)) and current_count:
            # point to the next available name for the link
            node.relink(link, f"{link.node_id}__{current_count}")
        else:
            # pass through link to ensure it updates to the normalized node ID
            node.relink(link, link.node_id)

    nodes.append(node)


def sort_tabs(tabs: list[dict]):
    tabs.sort(key=lambda tab: tab.get("tabProperties", {}).get("index", 999))


def parse_tab(
    tab: dict,
    tab_name_lookup: dict[str, str],
    tab_name_prefix: str,
    header_map: dict[str, str],
    seen_id_counter: dict[str, int],
) -> list[Node]:
    properties = tab.get("tabProperties", {})
    tab_id: str = properties.get("tabId", "")
    if properties.get("title", "Untitled").startswith(("-", ".")):
        return []  # Skip tabs that start with a dash or a dot, they are just for information or organization
    tab_path: str = tab_name_prefix + " > " + properties.get("title", tab_id)
    tab_name_lookup[tab_id] = tab_path
    logger.info("Parsing tab: %s", tab_path)

    raw_paragraphs = [
        i.get("paragraph", {})
        for i in tab.get("documentTab", {}).get("body", {}).get("content", [])
    ]
    paragraphs = [parse_raw_paragraph(p) for p in raw_paragraphs]
    last_header: str | None = None
    current_loop: int = 0  # Which loop are you on? e.g. Loop1
    current_track: str = ""  # Which track are you on? e.g. ResistAfter1
    node_title = None
    continue_node: bool = False
    node_content = ""
    node_links: list[Link] = []
    indent_offset = 0
    nodes: list[Node] = []

    # Test indentation scheme first for tab. Node title is either on the first indent or the second
    for i, p in enumerate(paragraphs):
        if i >= len(paragraphs) - 1:
            break
        indent = p.indent
        if not p.content:
            continue  # Skip empty paragraphs
        # first level indent, check if next paragraph is indent 2, if so, this is a node title
        if indent == 1 and paragraphs[i + 1].indent == 2:
            indent_offset = 1

    # Find all nodes in the tab, based on indentation.
    for p in paragraphs:
        indent: int = p.indent - indent_offset
        content: str = p.content
        name_candidate = normalize_id(content)

        if (indent <= 0 and name_candidate) or continue_node:
            if node_title and node_content.strip(" \t\n\r"):
                add_node(
                    tab_path,
                    nodes,
                    seen_id_counter,
                    node_title,
                    node_content,
                    node_links,
                    current_loop,
                    current_track,
                )
                if last_header:
                    header_map[last_header] = node_title
                    last_header = None
            if continue_node:
                name_candidate = "continue"
                continue_node = False
            current_index = seen_id_counter.get(name_candidate, 0)
            seen_id_counter[name_candidate] = current_index + 1
            if current_index:
                name_candidate = f"{name_candidate}__{current_index}"
            node_title = name_candidate
            node_content = ""
            node_links = []

            # Set last header to most recent node title after the node is processed
            if (
                node_title
                and not last_header
                and (match := re.search(r"^loop (\w+)-", node_title))
            ):
                try:
                    current_loop = int(match.group(1))
                    last_header = node_title
                    current_track = last_header.split("-")[-1]
                except ValueError:
                    logger.exception(
                        "Failed to parse loop number from node title: %s", node_title
                    )
        elif node_title and (indent == 1 or node_content.strip(" \t\n\r")):
            node_content += content

        node_links += p.links
        if any(l for l in p.links if l.node_id == ">>"):
            # Start a new node if the ">>" link is encountered
            continue_node = True

    # Add any remaining nodes
    if node_title and node_content:
        add_node(
            tab_path,
            nodes,
            seen_id_counter,
            node_title,
            node_content,
            node_links,
            current_loop,
            current_track,
        )
        if last_header:
            header_map[last_header] = node_title

    child_tabs = tab.get("childTabs", [])
    sort_tabs(child_tabs)
    for child_tab in child_tabs:
        nodes += parse_tab(
            child_tab, tab_name_lookup, tab_path, header_map, seen_id_counter
        )

    return nodes


def parse_document(doc_data: list[tuple[str, list[dict]]]) -> Document | None:
    nodes: list[Node] = []
    seen_id_counter: dict[str, int] = {}
    header_map: dict[str, str] = {}
    tab_name_lookup: dict[str, str] = {}
    start_node = None

    for title, tabs in doc_data:
        # Process each act's tabs individually
        sort_tabs(tabs)
        for tab in tabs:
            nodes += parse_tab(tab, tab_name_lookup, title, header_map, seen_id_counter)

    # After all nodes are created, try to resolve invalid links
    all_node_ids = {node.node_id for node in nodes}

    logger.info("Resolving links...")
    for i, node in enumerate(nodes):
        next_node = nodes[i + 1] if i + 1 < len(nodes) else None
        for link in node.links:
            if link.node_id not in all_node_ids:
                # 1. Check if the link has double underscores to see if the base node exists
                stripped_node_id = link.node_id.split("__")[0]
                if stripped_node_id in all_node_ids:
                    node.relink(link, stripped_node_id)
                # 2. Check if the node aligns with a header name
                elif header_node := header_map.get(stripped_node_id):
                    node.relink(link, header_node)
                # 2. Assign to the next node if there's only one link
                elif (
                    len(node.links) == 1
                    and next_node is not None
                    and node.tab_path == next_node.tab_path
                ):
                    node.relink(link, next_node.node_id)
                else:
                    logger.warning(
                        "Could not resolve link '%s' in node '%s' in tab '%s'",
                        link.node_id,
                        node.node_id,
                        node.tab_path,
                    )
        if (
            not node.links
            and next_node is not None
            and node.tab_path == next_node.tab_path
        ):
            # Node has no links at all, give the generic "wait for click" for now
            raw_text = f'<<run setup.waitForClick("{next_node.node_id}", 8)>>'
            node.content += f"\n\n{raw_text}"
            node.links.append(
                Link(
                    node_id=next_node.node_id,
                    display_name=">>",
                    raw_text=raw_text,
                )
            )

    if not nodes:
        return None

    if not start_node:
        start_node = nodes[0]

    return Document(nodes=nodes, tab_name_lookup=tab_name_lookup, start_node=start_node)


TITLE = "Haunting Test"
IF_ID = "CE1EFDB6-BA52-4AB2-8802-D5B83F26B74F"
STORY_HEADER = """:: StoryTitle
{title}
:: StoryData
{{
"ifid": "{interactive_fiction_id}",
"format": "SugarCube",
"format-version": "2.30",
"start": "{start_node}"
}}

:: StoryCaption
Tab: $tab_path
Current Loop: $current_loop
Generated: <<print setup.generatedAt>>

<<button "Reload">>
    <<run setup.auth.reload()>>
<</button>>
"""
GENERATED_TIMESTAMP_JS = """
setup.generatedAt = (new Date({generated_time})).toLocaleTimeString('en-US', {{month: 'numeric', day: 'numeric'}});
"""


async def generate_twee(gcp_service: GCPService) -> str:
    doc_data: list[tuple[str, list[dict]]] = []
    for act in global_twine_config.acts:
        logger.info("Processing act: %s", act.name)
        file_data = await gcp_service.get_file_by_id(act.file_id)
        if not file_data or not file_data.get("title"):
            logger.warning("Failed to retrieve document for act: %s", act.name)
            continue
        title = file_data.get("title", "")
        tabs = file_data.get("tabs", [])
        doc_data.append((title, tabs))

    document = parse_document(doc_data)

    if not document:
        logger.warning("No nodes found in the document.")
        return ""

    # Start with Header
    output = STORY_HEADER.format(
        title=TITLE,
        interactive_fiction_id=IF_ID,
        start_node=document.start_node.node_id if document.start_node else "Start",
    )

    # Append each node as a passage
    last_tab: str | None = None
    for node in document.nodes:
        if node.tab_path != last_tab:
            last_tab = node.tab_path
            output += f"\n::TAB BEGIN: {document.tab_name_lookup.get(node.tab_path, node.tab_path)}\n"
        output += f"\n:: {node.node_id}\n{node.content}\n"

    # Append JS and CSS sections
    async with aiofiles.open("story_script.js", "r") as js_file:
        js_content = await js_file.read()
    async with aiofiles.open("story_script_dev.js", "r") as dev_js_file:
        dev_js_content = await dev_js_file.read()

    generated_time = round(time.time() * 1000)
    output += f"\n\n:: StoryScript [script]\n{js_content}\n{dev_js_content}\n{GENERATED_TIMESTAMP_JS.format(generated_time=generated_time)}\n"

    async with aiofiles.open("story_styles.css", "r") as css_file:
        css_content = await css_file.read()
        output += f"\n\n:: StoryStylesheet [stylesheet]\n{css_content}\n"

    # Save to file
    await aiofiles.os.makedirs("build", exist_ok=True)
    async with aiofiles.open("build/twee_output.twee", "w") as f:
        await f.write(output)

    # Build twine file using tweego
    await generate_twine_file("build/twee_output.twee", "build/build.html")

    # Generate mermaid diagram for visualization
    # await build_mermaid_diagram(document, tabs)

    return output


async def generate_twine_file(twee_file: str, output_filename: str) -> None:
    executable_path = "tweego/tweego.exe" if os.name == "nt" else "tweego/tweego"
    command = [executable_path, twee_file, "-o", output_filename]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode == 0:
            logger.info("Twine file generated successfully.")
        else:
            logger.error("Error generating Twine file: %s", stderr.decode())
    except FileNotFoundError:
        logger.exception("Exception encountered generating Twine file")


async def build_mermaid_diagram(document: Document, tabs: dict):
    """Build a mermaid diagram from the document."""

    tab_name_lookup = {}

    def process_tab(tab: dict) -> None:
        properties = tab.get("tabProperties", {})
        tab_name_lookup[properties.get("tabId")] = properties.get("title")
        for child_tab in tab.get("childTabs", []):
            process_tab(child_tab)

    for tab in tabs:
        process_tab(tab)

    def clean_id(node_id: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_]", "", node_id)

    def generate_preview(node: Node) -> str:
        output = node.content
        for link in node.links:
            output = output.replace(link.raw_text, f"*{link.display_name}*")
        lines = output.splitlines()
        if len(lines) > 3:
            lines = lines[:3]
            lines[-1] += "..."
        return "<br>".join(lines)

    # Order nodes by their current loop and track for better visualization
    document.nodes.sort(key=lambda n: (n.current_loop, n.current_track or ""))

    diagram = "graph TD\n"
    current_section: str | None = None
    for node in document.nodes:
        node_section = (
            f'"Loop {node.current_loop} - {(node.current_track or "").title()}"'
        )
        if node_section != current_section:
            if current_section is not None:
                diagram += "end\n"
            current_section = node_section
            diagram += f"    subgraph {current_section}\n"
        node_id = clean_id(node.node_id)
        display_name = clean_id(node.node_id.split("__")[0])
        diagram += f'{node_id}["`**{display_name}**<br>{generate_preview(node)}`"]\n'
        for link in node.links:
            diagram += (
                f'{node_id} -->|"{link.display_name}"| {clean_id(link.node_id)}\n'
            )
    diagram += "end\n"

    async with aiofiles.open("build/diagram.mmd", "w") as f:
        await f.write(diagram)

    merman_engine = MermanEngine()
    svg_str = merman_engine.render_svg(diagram, '{"theme": "forest"}')

    async with aiofiles.open("build/diagram.svg", "w") as f:
        await f.write(svg_str)
