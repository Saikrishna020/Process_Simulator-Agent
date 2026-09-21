from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .model import public_model
from .schemas import ExperimentRequest
from .service import BusyError, workbench

router = APIRouter(prefix="/api/workbench")


def invoke(function, *args):
    try:
        return function(*args)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Requested dataset, model or experiment was not found.") from exc
    except BusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class DiscoverRequest(BaseModel):
    dataset: str


@router.get("/datasets")
def datasets():
    return workbench.datasets()


@router.post("/models", status_code=202)
def learn(body: DiscoverRequest):
    return invoke(workbench.submit, "discover", body.dataset)


@router.get("/models/{model_id}")
def model(model_id: str):
    return public_model(invoke(workbench.get_model, model_id))


@router.post("/experiments", status_code=202)
def experiment(body: ExperimentRequest):
    return invoke(workbench.submit, "experiment", body)


@router.get("/jobs")
def jobs():
    return workbench.history()


@router.get("/jobs/{job_id}")
def job(job_id: str):
    return invoke(workbench.job, job_id)


@router.get("/experiments/{job_id}")
def result(job_id: str):
    return invoke(workbench.result, job_id)


@router.get("/experiments/{job_id}/files/{filename}")
def download(job_id: str, filename: str):
    path = invoke(workbench.download, job_id, filename)
    return FileResponse(path, filename=filename)
