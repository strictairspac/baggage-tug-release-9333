from fastapi import APIRouter, Depends, Response

from src.config.resources import engine
from src.controllers.access import identity, operator, reviewer
from src.controllers.workbench import new_archive, new_case, new_run
from src.schemas.archives import ArchiveEntry
from src.schemas.cases import CaseEntry, CaseInput
from src.schemas.runs import RunEntry, RunInput
from src.services import archives, cases

router = APIRouter(prefix="/cases", tags=["Cases"])


@router.get("", response_model=list[CaseEntry], dependencies=[Depends(identity)])
def list_cases():
    with engine.connect() as db:
        return cases.list_entries(db)


@router.get("/{case_id}", response_model=CaseEntry, dependencies=[Depends(identity)])
def case_detail(case_id: int):
    with engine.connect() as db:
        return cases.get(db, case_id)


@router.post("", response_model=CaseEntry, status_code=201)
def register(form: CaseInput, user=Depends(operator)):
    return new_case(form, user)


@router.post("/{case_id}/runs", response_model=RunEntry, status_code=202)
def schedule(case_id: int, form: RunInput, user=Depends(operator)):
    return new_run(case_id, form, user)


@router.get("/{case_id}/archive", response_model=ArchiveEntry | None, dependencies=[Depends(identity)])
def archive_detail(case_id: int):
    with engine.connect() as db:
        cases.get(db, case_id)
        return archives.get(db, case_id)


@router.post("/{case_id}/archive", response_model=ArchiveEntry, status_code=201)
def start_archive(case_id: int, response: Response, user=Depends(reviewer)):
    job, created = new_archive(case_id, user)
    if not created:
        response.status_code = 200
    return job
