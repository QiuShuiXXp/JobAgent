from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "jinja2"


class PromptLoader:

    def __init__(self,prompts_dir:Path=PROMPTS_DIR):
        self.env=Environment(
            loader=FileSystemLoader(str(prompts_dir)),
            undefined=StrictUndefined,
            autoescape=False
        )
        self._cache:dict[str,str]={}


    def get(self,name:str):
        if name not in self._cache:
            self._cache[name]= self.env.get_template(name).render()
        return self._cache[name]

    def raw(self,name:str)->str:
        """读模板原文不渲染——LLM 提示词模板（带 {{ 变量 }} 占位符）专用：
        二级渲染由 llm_client.ainvoke_json 内部的 PromptTemplate(jinja2) 完成，
        加载期渲染会 StrictUndefined 报错。clarify 确定性话术（无变量）才用 get()。
        """
        return (PROMPTS_DIR / name).read_text(encoding="utf-8")

    def render(self,name:str,**kwargs)->str:
        tmpl=self.env.get_template(name)
        return tmpl.render(**kwargs)

    def dry_render_check(self) -> None:
        """启动期自检：所有 .j2 模板可被 jinja2 加载编译（语法检查，不渲染）。"""
        for name in self.env.list_templates():
            if name.endswith(".j2"):
                self.env.get_template(name)
