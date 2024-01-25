# AutomatedRelease

## Authentication Instructions
	
 1.  Github Token Verification
     - You need a GitHub Personal Access Token to authenticate Python with GitHub. If you don't have a token, follow the steps below to generate one:
     - Visit [GitHub Personal Access Tokens](https://github.com/settings/tokens) page.
     - Click on "Generate token" and provide the give acsess to 'repos' and 'read:org'
     - Copy the generated token.
     - When prompted by the python file, enter your token 
## Details

     * First, the GitHub class is created to make a connection to the GitHub API
     * The GitHub class is passed a users PA token in order to bypass rate limits
     * Using methods from the GitHub class, the program retrieves the organizations name and repos
     * Using the 'git -ls remote' command, the program retrieves the SHA from Github using the repo url
     * The repo.get_commit() method is used to get a specific commit from the SHA
 
 

