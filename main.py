import re
import subprocess

from github import Github
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import os


def get_sprintdates():
    today = datetime.now(timezone.utc)

    ##test cases
    # today=datetime(2024,2,7,tzinfo=timezone.utc) #(date in even week that  shouldnt be output) outputs 2024-01-15 tom 2024-01-26
    # today=datetime(2024,1,30,tzinfo=timezone.utc) # start in odd week OUTPUTS 2024-01-15 to 2024-01-26
    # today = datetime(2024, 1, 29, tzinfo=timezone.utc) #2024-01-15 to 2024-01-26
    #today = datetime(2024, 1, 29, tzinfo=timezone.utc) #2024-01-15 to 2024-01-26
    # today = datetime(2024, 1, 28, tzinfo=timezone.utc) #2024-01-01 to 2024-01-12 NOW 2024-01-15 to 2024-01-26 saturday case
    # today = datetime(2024, 1, 26, tzinfo=timezone.utc) #2024-01-01 to 2024-01-12 correct
    # today = datetime(2024, 1, 21, tzinfo=timezone.utc) # 2024-02-12 to 2024-02-23
    # today = datetime(2024, 2, 10, tzinfo=timezone.utc) # 2024-01-29 to 2024-02-09
    #today = datetime(2023, 6, 14, tzinfo=timezone.utc) # 2024-01-29 to 2024-02-09



    first_day_of_year = datetime(today.year, 1, 1, tzinfo=timezone.utc)
    full_weeks_since_year_start = (today - first_day_of_year).days // 7




    ## We add a plus one because the integer divison can only find what week you are in by measuring how many full weeks have passed,
    ## and so we still need to account for the current week we are in
    week_number=full_weeks_since_year_start+1

    ##gets find_following_monday to right week
    find_following_monday = first_day_of_year + timedelta(weeks=week_number)
    ##gets find_following_monday to the right monday(this shouldnt matter in 2024, i added in case other years mess somthing up)
    find_following_monday = find_following_monday - timedelta(days=find_following_monday.weekday())

    find_following_monday -= timedelta(weeks=4) if week_number % 2 == 0 else timedelta(weeks=3)

    sprint_start = find_following_monday
    sprint_end = sprint_start + timedelta(days=4) + timedelta(weeks=1)

    print(f'Week Number: {week_number}')
    return sprint_start, sprint_end

def fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date):
    sprint_commits = []
    errorlist=[]
    errordict={}

    try:
        dev_branch = repo.get_branch("dev")
        #print(f"Fetching commits for {repo.full_name}...")
        commits = repo.get_commits(since=sprint_start_date, until=sprint_end_date, sha=dev_branch.commit.sha)
        for commit in commits:
            commit_date = commit.commit.author.date
            sprint_commits.append(('dev', commit_date, commit.commit.message.split('\n')[0]))


    except Exception as e:
        ##outputs list of repos where no commits found, no dev branch
        errorlist.append((repo.full_name,e))




    return sprint_commits,errorlist



def print_commits():


    orgname = 'pointblue'
    errorlist=[]
    load_dotenv()

    github_token = os.getenv('GITHUB_TOKEN')
    ###if the program can't find github token, it checks if path to dotenv file exists, if not, then it creates one and configures it


    sprint_start_date, sprint_end_date = get_sprintdates()

    ###THESE 2 lines  CAN BE USED TO TEST THIS PROGRAM, just change date to date range you want to test
    #sprint_start_date = datetime(datetime.now().year, 3, 1, tzinfo=timezone.utc)
    #sprint_end_date = datetime(datetime.now().year, 2, 23, tzinfo=timezone.utc)


    print(f'Current sprint: {sprint_start_date.strftime("%Y-%m-%d")} to {sprint_end_date.strftime("%Y-%m-%d")}')
    if not github_token:
        envpath = os.path.exists('env')
        print('No Github Token')
        if not (envpath):
            with open('.env', 'w') as fh:
                github_token = input(
                    'Please enter your GitHub personal access token so that it can be stored for later use: ')
                fh.write(f'GITHUB_TOKEN={github_token}')
                print('If you are pushing commits to this code, remember to add your .env file to .gitignore')

        else:
            print('Please add your GitHub token to your dotenv file')

    g = Github(github_token)
    org = g.get_organization(orgname)

    for repo in org.get_repos():
        try:
            ##stores the date, title,and branch in commits in branch dev from sprint period
            out,errors = fetch_commits_within_sprint(repo, sprint_start_date, sprint_end_date)
            ##appends errorlist with error messages that are not empty
            if(errors):
                errorlist.extend(errors)

            if (out):
                print(f"Repository: {repo.full_name}")
                for branch_name, commit_date, commit_title in out:
                    formatted_date = commit_date.strftime('%Y-%m-%d %H:%M:%S %Z')
                    print(f'Branch: {branch_name}, Date: {formatted_date}, Title: {commit_title}')
                print('=' * 50)

        except Exception as e:
            print(f'Error fetching  for repo  {repo.full_name} : {e}')
    for repo,error in errorlist:
        if isinstance(error, Exception):
            if hasattr(error, 'data') and 'message' in error.data:
                error_message = error.data['message']
                if not "Branch not found" in error_message:
                    print(f'Error in repository {repo}: {error}')
        else:

            pass




        # Check if there are branches with no commits in the last two weeks and update last_commit_date
print_commits()
print('END')

