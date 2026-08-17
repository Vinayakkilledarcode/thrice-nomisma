import java.util.*;
class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n=sc.nextInt();
        int[] arr1= new int[n];
        
        
        for(int i=0;i<n;i++){
            arr1[i]=sc.nextInt();
        }
        int k=sc.nextInt();
        int[] temp = new int[n];
        int[] temp1 = new int[n];
        for(int i=0;i<n;i++){
            temp[i] = arr1[(i + k)%n];
            temp1[(i + k)%n] = arr1[i];
        }
        for(int x:temp){
            System.out.print(x+" ");
        }
        System.out.println("\n");
        for(int x:temp1){
            System.out.print(x+" ");
        }
    }
}