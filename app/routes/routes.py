from fastapi import APIRouter, HTTPException, status
from models.models import Jobs, JobCreate
import uuid

router = APIRouter(prefix="/test_jobs")

test_jobs: list[Jobs] = []


@router.post(
    "/jobs",
    status_code=status.HTTP_201_CREATED,
    response_model=Jobs,
    summary="Create a new jobs test execution"
)
def create_job(payload: JobCreate):
    try:
        """
        This part is to save on the database
        """
        job = Jobs(
            id=str(uuid.uuid4()),
            url=payload.url,
            status="PENDING",
            test_scenarios=[]
        )
        test_jobs.append(job)

    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Something gone wrong: {error}"
        )

    return job

@router.get(
    "/get_job/{id}",
    status_code=status.HTTP_200_OK,
    response_model=Jobs,
    summary="Get job detail"
)
def get_job(id: str):
    """
    Query to the database
    """
    job_detail = next((job for job in test_jobs if job.id == id), None)

    if job_detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ID: {id} no exist on the database"
        )

    return job_detail
