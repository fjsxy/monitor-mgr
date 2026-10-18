export type Account={id:string;username:string;role:'ADMIN'|'USER';enabled:boolean;mustChangePassword:boolean;createdAt:string};
export type Session={account:Account;expiresAt:string};
export class APIError extends Error{constructor(public code:string,message:string){super(message)}}
export async function api<T>(path:string,method='GET',body?:unknown):Promise<T>{
 const response=await fetch(`/api/v1${path}`,{method,credentials:'same-origin',headers:body?{'Content-Type':'application/json'}:{},body:body===undefined?undefined:JSON.stringify(body)});
 const value=await response.json();if(!response.ok)throw new APIError(value.code,value.message);return value as T;
}
