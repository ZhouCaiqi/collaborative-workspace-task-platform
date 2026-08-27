from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI()
tasks = []
next_task_id = 1

class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    completed: bool = False
    priority: int = Field(default=1, ge=1, le=5)

class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    completed: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=5)

class TaskResponse(BaseModel):
    id: int
    title: str
    completed: bool
    priority: int

@app.get("/tasks", response_model=list[TaskResponse])
def get_tasks(
    completed: bool | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    priority: int | None = Query(default=None, ge=1, le=5)
):
    result = tasks
    if completed is not None:
        result = [task for task in result if task["completed"] == completed]
    if priority is not None:
        result = [task for task in result if task["priority"] == priority]
    return result[:limit]

@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: int):
    for task in tasks:
        if task["id"] == task_id:
            return task

    raise HTTPException(
        status_code=404,
        detail="Task not found"
    )

@app.post("/tasks", status_code=201, response_model=TaskResponse)
def create_task(task: TaskCreate):
    global next_task_id
    task_dic = task.model_dump()
    task_dic["id"] = next_task_id
    next_task_id += 1
    tasks.append(task_dic)
    return task_dic

@app.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, task_update: TaskUpdate):
    update_data = task_update.model_dump(exclude_unset=True)
    for task in tasks:
        if task["id"] == task_id:
            task.update(update_data)
            return task
    raise HTTPException(
        status_code=404,
        detail="Task not found"
    )

@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int):
    for index, task in enumerate(tasks):
        if task["id"] == task_id:
            tasks.pop(index)
            return
    raise HTTPException(
            status_code=404,
            detail="Task not found"
        )